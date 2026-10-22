"""Public interface of Component 2 (Deployment Topology Modeling).

Data sources are never mixed silently:
- live:   the Kubernetes API (raises ClusterUnavailableError when no cluster can be reached)
- sample: sample_data/deployment.json, only through the *_sample functions
"""
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.integrations.kubernetes.client import ClusterAccessError
from app.shared.evidence_errors import EvidenceError, unavailable
from app.engine.deployment_topology_modeling import discovery
from app.engine.deployment_topology_modeling.discovery import DiscoveryResult
from app.shared.schemas.deployment import DeploymentGraph
from app.engine.deployment_topology_modeling import placement_metrics as pm
from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterNode
from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import get_cluster_nodes, get_cluster_pods
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import resolve_deployment_ownership
from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import analyze_replica_concentration
from app.engine.deployment_topology_modeling.graph_builder import DeploymentGraphBuilder
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_parser import parse_declared_intents
from app.engine.deployment_topology_modeling.manifest_parsing.pipeline import run_manifest_pipeline
from app.engine.deployment_topology_modeling.drift_analysis.constants import DriftType, INTENT_DRIFT_TYPES
from app.engine.deployment_topology_modeling.drift_analysis.drift_analyzer import DriftAnalyzer
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ClusterExposureReport,
    BaselineB2Result,
    ModelAccuracyCertificate,
    PlacementDriftDiffReport,
)
from app.engine.deployment_topology_modeling.drift_analysis.placement_drift_diff_engine import (
    PlacementDriftDiffEngine,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "deploy" / "kubernetes" / "boutique.yaml"
FALLBACK_MANIFEST_DIR = PROJECT_ROOT / "deploy" / "test-workloads"
SAMPLE_FILE = PROJECT_ROOT / "sample_data" / "deployment.json"
LIVE_EXPERIMENT_RESULTS_DIR = PROJECT_ROOT / "experiments" / "placement_drift" / "results" / "five-repetitions"
# The real-application check: the same live runs on the team's Bookinfo benchmark.
LIVE_EXPERIMENT_BOOKINFO_DIR = PROJECT_ROOT / "experiments" / "placement_drift" / "results" / "bookinfo-five-repetitions"
HOSTNAME_LABEL = "kubernetes.io/hostname"


class ClusterUnavailableError(RuntimeError):
    """No Kubernetes cluster could be reached. Callers must not substitute sample data silently."""


class CodedClusterUnavailableError(ClusterUnavailableError, discovery.DiscoveryError):
    """The cluster could not be read, reported both ways.

    It is a ClusterUnavailableError, so everything written against that (the 503 route class, the safety
    status) keeps working, and it is a DiscoveryError, so the stable code (CLUSTER_NOT_CONNECTED,
    CLUSTER_AUTH_FAILED, ...) reaches the extension's pages unchanged.
    """

    def __init__(self, error: discovery.DiscoveryError) -> None:
        discovery.DiscoveryError.__init__(
            self, error.code, error.message, http_status=error.http_status,
            retryable=error.retryable, details=error.details,
        )


@dataclass
class TopologyAnalysis:
    graph: DeploymentGraph
    report: ClusterExposureReport
    baseline_b2: BaselineB2Result
    data_source: str


# ---------------------------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------------------------

def collect_live_cluster(namespace: str = "default") -> tuple[list[ClusterNode], list[dict[str, Any]]]:
    """Worker nodes and analysed services of one namespace from the live cluster.

    Everything is read through the one cluster source (discovery.get_cluster_source), so this path and
    discover_live() see the same cluster, and a test that installs a fake cluster covers both.
    """
    if not discovery.NAMESPACE_PATTERN.match((namespace or "").strip()):
        raise discovery.DiscoveryError(
            "INVALID_NAMESPACE", "The namespace is not a valid Kubernetes namespace name.", http_status=422
        )
    try:
        source = discovery.get_cluster_source()
        nodes = get_cluster_nodes(source)
        pods = get_cluster_pods(namespace=namespace, source=source)
        workloads = resolve_deployment_ownership(pods=pods, namespace=namespace, source=source)
    except ClusterAccessError as exc:
        # NotConnected, ConnectionFailed, AuthFailed, Forbidden and ApiFailed all come from the REST
        # client with messages that never carry credentials. The caller must not substitute sample data.
        raise CodedClusterUnavailableError(discovery.classify_access_error(exc)) from None
    except ConnectionError as exc:
        raise ClusterUnavailableError(f"Kubernetes API is unreachable: {exc}") from exc
    except discovery.DiscoveryError:
        raise
    except Exception:
        # The exception text may contain request details or a credential; it stays in the server log and
        # the caller gets the stable code only.
        logger.exception("Unexpected failure while reading the cluster")
        raise CodedClusterUnavailableError(discovery.DiscoveryError(
            "DISCOVERY_FAILED", "The cluster returned an unexpected error during discovery.",
            http_status=502, retryable=True,
        )) from None
    return nodes, analyze_replica_concentration(workloads, {n.name: n.ready for n in nodes})


# ---------------------------------------------------------------------------------------------
# Live discovery for the Docker Desktop extension
#
# The functions above build a graph per namespace and raise ClusterUnavailableError. The extension
# needs more: a status envelope saying how complete the discovery was, and a coded EvidenceError (never
# a bare 500) for every way the cluster can be unreadable. discover_live() provides that on top of
# discovery.py, which reads the cluster through the REST client and reports whatever it could not
# resolve instead of guessing. Both paths stay available and read the same cluster.
# ---------------------------------------------------------------------------------------------

COMPONENT = "deployment_topology"


def _declared_intent(manifest_path: str | None) -> dict[str, Any]:
    """Declared deployment intent from an explicitly selected source, or none.

    No default manifest is ever read. Runtime discovery works from live cluster state alone; declared
    intent is optional evidence that the caller must name.
    """
    if manifest_path is None:
        return {}
    directory = Path(manifest_path)
    if not directory.is_dir():
        raise unavailable(
            "DECLARED_STATE_UNAVAILABLE",
            "The selected declared-state source does not exist or is not a directory.",
            COMPONENT,
        )
    intents = run_manifest_pipeline(str(directory))
    if not intents:
        raise unavailable(
            "DECLARED_STATE_UNAVAILABLE",
            "The selected declared-state source contains no readable deployment manifests.",
            COMPONENT,
        )
    return {m.service_name: m for m in intents}


def _pods_by_service(graph: DeploymentGraph) -> dict[str, list[dict[str, Any]]]:
    return {
        svc.name: [{"name": p.name, "node": p.node, "ready": p.ready} for p in svc.pods]
        for svc in graph.services
    }


def _nodes_for_drift(graph: DeploymentGraph) -> list[dict[str, Any]]:
    return [
        {"name": n.name, "zone": n.zone, "labels": {"kubernetes.io/hostname": n.name}}
        for n in graph.nodes
    ]


def discover_live(
    namespace: str = "default",
    manifest_path: str | None = None,
) -> DiscoveryResult:
    """Discovery envelope for the connected cluster: the DeploymentGraph plus how much was resolved.

    Raises ``DiscoveryError`` when the cluster cannot be read; it never substitutes sample data.
    Drift is checked against declared intent only when ``manifest_path`` names a source.
    """
    start_time = time.perf_counter()
    result = discovery.discover(namespace)
    manifest_map = _declared_intent(manifest_path)

    cluster_report, _ = DriftAnalyzer().analyze_cluster(
        manifests_by_service=manifest_map,
        runtime_pods_by_service=_pods_by_service(result.graph),
        cluster_nodes=_nodes_for_drift(result.graph),
    )
    # Same rule as the per-namespace graph builder: drift means declared intent differs from runtime
    # (scale, affinity, spread). Informational findings such as missing resource limits are not drift.
    drift_by_service = _drift_reports(cluster_report)
    for svc in result.graph.services:
        drift = drift_by_service.get(svc.name)
        if drift is not None:
            svc.drift_detected = drift["drift_detected"]
            svc.drift_reasons = drift["drift_reasons"]

    result.declared_state = "available" if manifest_map else "unavailable"
    result.graph.validation.runtime_overhead_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
    return result


def _associate_manifest_intents(
    parsed_manifests: list[Any], analyzed_services: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Associates Deployment manifests with Services through the Deployments that back each
    Service (resolved from owner references), never by name similarity."""
    by_deployment = {(m.namespace, m.service_name): m for m in parsed_manifests}
    drift_manifests: dict[str, Any] = {}
    service_intents: dict[str, dict[str, Any]] = {}
    for service in analyzed_services:
        service_name = service.get("name")
        namespace = service.get("namespace", "default")
        matching = [
            by_deployment[(namespace, name)] for name in service.get("deploymentNames", [])
            if (namespace, name) in by_deployment
        ]
        if not matching or not service_name:
            continue
        primary = matching[0]
        expected_replicas = sum(item.expected_replicas for item in matching)
        spread = [c for item in matching for c in item.spread_constraints]
        requests = [r for item in matching for r in item.resource_requests]
        limits = [lim for item in matching for lim in item.resource_limits_by_container]
        drift_manifests[service_name] = primary.model_copy(update={
            "service_name": service_name,
            "expected_replicas": expected_replicas,
            "spread_constraints": spread,
            "has_node_affinity": any(item.has_node_affinity for item in matching),
            "pod_anti_affinity_configured": any(item.pod_anti_affinity_configured for item in matching),
            "pod_affinity_configured": any(item.pod_affinity_configured for item in matching),
            "resource_requests": requests,
            "resource_limits_by_container": limits,
        })
        service_intents[service_name] = {
            "expected_replicas": expected_replicas,
            "selector": primary.selector,
            "readiness_probe_configured": all(item.readiness_probe_configured for item in matching),
            "has_node_affinity": any(item.has_node_affinity for item in matching),
            "pod_anti_affinity_configured": any(item.pod_anti_affinity_configured for item in matching),
            "pod_affinity_configured": any(item.pod_affinity_configured for item in matching),
            "node_selector": primary.node_selector,
            "tolerations": primary.tolerations,
            "selector_match_expressions": primary.selector_match_expressions,
            "node_affinity_rules": primary.node_affinity_rules,
            "pod_affinity_rules": primary.pod_affinity_rules,
            "pod_anti_affinity_rules": primary.pod_anti_affinity_rules,
            "spread_constraints": [c.model_dump() for c in spread],
            "resource_requests": requests,
            "resource_limits_by_container": limits,
        }
    return drift_manifests, service_intents


# Where declared intent (replicas, nodeSelector, affinity, topology spread) comes from, in this order:
#   1. a manifest file or folder the caller names (manifest_path),
#   2. the one the operator named with RESILIENCE_MANIFEST_PATH (set on the backend, never taken from a
#      request, so a client cannot make the server read an arbitrary path),
#   3. the repository's own manifests (DEFAULT_MANIFEST_PATH), which exist only in a repository checkout,
#   4. the Deployment specs of the connected cluster itself.
# The last one is the real source for the packaged extension, which ships no manifest folder. The
# Kubernetes API returns a Deployment in the same schema as a manifest, so the same parser reads it. It is
# the intent as applied to the cluster; a manifest in git can differ from it, which is why 1-3 win.
MANIFEST_PATH_ENV = "RESILIENCE_MANIFEST_PATH"


def _declared_target(manifest_path: str | None) -> Path | None:
    """The manifest file or folder to read, or None when the cluster's own Deployment specs are the source."""
    for candidate in (manifest_path, os.environ.get(MANIFEST_PATH_ENV)):
        if candidate:
            return Path(candidate)  # a named source is used, or reported empty; it never falls through
    return DEFAULT_MANIFEST_PATH if DEFAULT_MANIFEST_PATH.exists() else None


def get_manifest_source() -> dict[str, Any]:
    """Where declared intent comes from (same order as _declared_target), so the dashboard can say so."""
    target = _declared_target(None)
    if target is None:
        return {"source": "cluster", "path": None, "found": True}
    try:
        shown = target.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        shown = target.name
    return {"source": "manifest", "path": shown, "found": target.exists()}


def _cluster_declared_intents(namespace: str) -> list[Any]:
    """Declared intent read from the Deployments of the connected cluster (see the source order above)."""
    if not discovery.NAMESPACE_PATTERN.match((namespace or "").strip()):
        raise discovery.DiscoveryError(
            "INVALID_NAMESPACE", "The namespace is not a valid Kubernetes namespace name.", http_status=422
        )
    try:
        items = discovery.get_cluster_source().list_objects(
            "deployments", f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments"
        )
    except ClusterAccessError as exc:
        raise CodedClusterUnavailableError(discovery.classify_access_error(exc)) from None
    # List items carry no kind or apiVersion, and status is observed state, not declared intent.
    documents = [{"apiVersion": "apps/v1", "kind": "Deployment", **{k: v for k, v in item.items() if k != "status"}}
                 for item in items]
    return parse_declared_intents(documents)


def _declared_intents(manifest_path: str | None, namespace: str) -> list[Any]:
    target = _declared_target(manifest_path)
    if target is None:
        return _cluster_declared_intents(namespace)
    if not target.exists():
        return []
    try:
        return run_manifest_pipeline(str(target))
    except Exception as exc:
        raise ValueError(f"Unable to parse deployment manifest '{target}': {exc}") from exc


def _load_manifest_intents(
    manifest_path: str | None, analyzed_services: list[dict[str, Any]], namespace: str = "default"
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    parsed = _declared_intents(manifest_path, namespace)
    if not parsed:
        return {}, {}
    return _associate_manifest_intents(parsed, analyzed_services)


def _detector_nodes(nodes: list[ClusterNode]) -> list[dict[str, Any]]:
    """Nodes for the detectors. The hostname label is only filled in when the node lacks it."""
    return [
        {"name": n.name, "zone": n.zone, "labels": {HOSTNAME_LABEL: n.name, **(n.labels or {})}}
        for n in nodes
    ]


def _drift_reports(report: ClusterExposureReport) -> dict[str, dict[str, Any]]:
    """drift_detected = declared intent differs from runtime (scale, affinity, spread).
    Informational findings (missing resource limits) are excluded."""
    reports = {}
    for exp in report.services:
        intent_violations = [v for v in exp.violations if v.drift_type in INTENT_DRIFT_TYPES]
        reports[exp.service_name] = {
            "drift_detected": bool(intent_violations),
            "drift_reasons": [v.description for v in intent_violations],
        }
    return reports


def _analyse(
    nodes: list[ClusterNode],
    analyzed_services: list[dict[str, Any]],
    manifest_path: str | None,
    data_source: str,
    start_time: float,
    load_manifests: bool = True,
    namespace: str = "default",
) -> TopologyAnalysis:
    manifest_map, service_intents = (
        _load_manifest_intents(manifest_path, analyzed_services, namespace) if load_manifests else ({}, {})
    )
    report, baseline_b2 = DriftAnalyzer().analyze_cluster(
        manifests_by_service=manifest_map,
        runtime_pods_by_service={s["name"]: s.get("pods", []) for s in analyzed_services if s.get("name")},
        cluster_nodes=_detector_nodes(nodes),
    )
    if not manifest_map:
        # Nothing was declared to compare the cluster against. Say so, and never let that read as 100%
        # compliance: exposure and concentration still come from actual placement.
        report = report.model_copy(update={"constraint_compliance_rate": None, "declared_state": "unavailable"})
    builder = DeploymentGraphBuilder()
    builder.attach_nodes(nodes)
    builder.attach_services(analyzed_services, _drift_reports(report), service_intents)
    elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
    return TopologyAnalysis(builder.build(runtime_overhead_ms=elapsed_ms), report, baseline_b2, data_source)


def analyze_live_topology(namespace: str = "default", manifest_path: str | None = None) -> TopologyAnalysis:
    """Graph, exposure report and Baseline B2 computed from one live collection."""
    start_time = time.perf_counter()
    nodes, analyzed = collect_live_cluster(namespace)
    return _analyse(nodes, analyzed, manifest_path, "live", start_time, namespace=namespace)


def get_live_deployment_graph(namespace: str = "default", manifest_path: str | None = None) -> DeploymentGraph:
    """The validated DeploymentGraph handed to Components 3 and 4."""
    return analyze_live_topology(namespace, manifest_path).graph


def analyze_resilience_drift(
    namespace: str = "default", manifest_path: str | None = None
) -> tuple[ClusterExposureReport, BaselineB2Result]:
    """Cluster exposure report and Baseline B2 for the live cluster."""
    analysis = analyze_live_topology(namespace, manifest_path)
    return analysis.report, analysis.baseline_b2


def diff_cluster_snapshots(
    baseline_snapshot: ClusterExposureReport | dict,
    target_snapshot: ClusterExposureReport | dict,
) -> PlacementDriftDiffReport:
    """Placement drift rate between two exposure reports."""
    return PlacementDriftDiffEngine.diff_snapshots(baseline_snapshot, target_snapshot)


# ---------------------------------------------------------------------------------------------
# Sample fixture (explicitly labelled; never used as a hidden fallback)
# ---------------------------------------------------------------------------------------------

def get_sample_deployment() -> DeploymentGraph:
    """The static contract fixture in sample_data/deployment.json."""
    if SAMPLE_FILE.exists():
        return DeploymentGraph.model_validate(json.loads(SAMPLE_FILE.read_text(encoding="utf-8")))
    return DeploymentGraph(nodes=[], services=[])


def analyze_sample_topology() -> TopologyAnalysis:
    """Runs the same exposure analysis on the sample fixture (no declared intent available)."""
    start_time = time.perf_counter()
    sample = get_sample_deployment()
    nodes = [
        ClusterNode(name=n.name, zone=n.zone, cpu_available=str(n.cpu_available),
                    memory_available=str(n.memory_available), labels=n.labels, ready=n.ready)
        for n in sample.nodes
    ]
    workloads = {
        s.name: {
            "name": s.name,
            "namespace": s.namespace,
            "declaredReplicas": s.declared_replicas,
            "pods": [{"name": p.name, "node": p.node, "ready": p.ready, "phase": p.phase,
                      "restartCount": p.restart_count, "namespace": p.namespace} for p in s.pods],
            "deploymentNames": s.deployment_names,
        }
        for s in sample.services
    }
    analyzed = analyze_replica_concentration(workloads, {n.name: n.ready for n in nodes})
    return _analyse(nodes, analyzed, manifest_path=None, data_source="sample",
                    start_time=start_time, load_manifests=False)


# ---------------------------------------------------------------------------------------------
# Manifest intent
# ---------------------------------------------------------------------------------------------

def get_manifest_intent(namespace: str = "default") -> list[dict[str, Any]]:
    """Declared Deployment intent, from the source order described above (named manifests, the repository's
    own, else the connected cluster's Deployment specs). An empty list means nothing is declared there."""
    intents = [i.model_dump() for i in _declared_intents(None, namespace)]
    if not intents:
        raise unavailable(
            "DECLARED_STATE_UNAVAILABLE",
            "No declared-state source (deployment manifests or cluster Deployments) is available.",
            COMPONENT,
        )
    return intents


def get_manifest_service_detail(service_name: str, namespace: str = "default") -> dict[str, Any] | None:
    return next((i for i in get_manifest_intent(namespace) if i.get("service_name") == service_name), None)


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------

def get_academic_accuracy_certificate() -> ModelAccuracyCertificate:
    """Accuracy against the stored, kubectl-inspected KinD snapshot in
    experiments/single-node-failure/concentrated-baseline (see ground_truth_evaluation)."""
    from app.engine.deployment_topology_modeling.ground_truth_evaluation import evaluate_snapshot
    return evaluate_snapshot().certificate


def get_ground_truth_evaluation() -> dict[str, Any]:
    from app.engine.deployment_topology_modeling.ground_truth_evaluation import (
        KIND_CONCENTRATED_BASELINE,
        evaluate_snapshot,
    )
    evaluation = evaluate_snapshot()
    return {
        "ground_truth_source": KIND_CONCENTRATED_BASELINE.relative_to(PROJECT_ROOT).as_posix(),
        "pods_evaluated": evaluation.pods_evaluated,
        "services_evaluated": evaluation.services_evaluated,
        "failure_experiments": evaluation.failure_experiments,
        "certificate": evaluation.certificate.model_dump(),
        "exposure_checks": evaluation.exposure_checks,
    }


# ---------------------------------------------------------------------------------------------
# Views over one live analysis
# ---------------------------------------------------------------------------------------------

def get_cluster_zones(namespace: str = "default") -> list[dict[str, Any]]:
    """Zones, their nodes and hosted pod counts. Nodes without a zone label are 'unassigned-zone'."""
    graph = get_live_deployment_graph(namespace=namespace)
    zone_map: dict[str, dict[str, Any]] = {}
    for node in graph.nodes:
        z = node.zone or "unassigned-zone"
        zone_map.setdefault(z, {"zone": z, "nodes": [], "total_pods": 0, "services": set()})
        zone_map[z]["nodes"].append(node.name)
    for svc in graph.services:
        for p in svc.pods:
            z = p.zone or "unassigned-zone"
            if z in zone_map:
                zone_map[z]["total_pods"] += 1
                zone_map[z]["services"].add(svc.name)
    return [
        {"zone": v["zone"], "nodes": v["nodes"], "node_count": len(v["nodes"]),
         "total_pods": v["total_pods"], "hosted_services": sorted(v["services"])}
        for v in zone_map.values()
    ]


def get_node_detail(node_name: str, namespace: str = "default") -> dict[str, Any] | None:
    graph = get_live_deployment_graph(namespace=namespace)
    match_node = next((n for n in graph.nodes if n.name == node_name), None)
    if not match_node:
        return None
    hosted_pods = [
        {"pod_name": p.name, "service_name": svc.name, "is_ready": p.ready, "restart_count": p.restart_count}
        for svc in graph.services for p in svc.pods if p.node == node_name
    ]
    services = sorted({p["service_name"] for p in hosted_pods})
    return {
        "node_name": match_node.name,
        "zone": match_node.zone,
        "ready": match_node.ready,
        "schedulable": match_node.schedulable,
        "cpu_available": match_node.cpu_available,
        "memory_available": match_node.memory_available,
        "total_pods": len(hosted_pods),
        "total_services": len(services),
        "services": services,
        "pods": hosted_pods,
    }


def get_drift_spofs(namespace: str = "default") -> list[dict[str, Any]]:
    """Services whose ready replicas all run on one node (exposed to that node's failure)."""
    return spofs_from_report(analyze_resilience_drift(namespace=namespace)[0])


def spofs_from_report(report: ClusterExposureReport) -> list[dict[str, Any]]:
    return [
        {
            "service_name": svc.service_name,
            "replica_concentration": svc.replica_concentration,
            "ready_replicas": svc.ready_replicas,
            "hosting_nodes": svc.exposed_failure_domains,
            "exposure_level": svc.exposure_level.value,
            "reason": (f"All {svc.ready_replicas} ready replica(s) run on node "
                       f"{', '.join(svc.exposed_failure_domains)}."),
        }
        for svc in report.services if svc.exposure_status == pm.ExposureStatus.EXPOSED
    ]


_CATEGORY_BY_TYPE = {
    DriftType.SCALE_DRIFT: "scale_drift",
    DriftType.AFFINITY_DRIFT: "node_affinity_drift",
    DriftType.TOPOLOGY_SPREAD_DRIFT: "topology_spread_violations",
    DriftType.RESOURCE_CONTENTION_DRIFT: "resource_limit_drift",
}


def get_drift_categories_summary(namespace: str = "default") -> dict[str, Any]:
    """Violations grouped by detector. `resource_contention` stays empty: no detector measures
    node contention (the resource detector only reports missing limits)."""
    return categories_from_report(analyze_resilience_drift(namespace=namespace)[0])


def categories_from_report(report: ClusterExposureReport) -> dict[str, Any]:
    categories: dict[str, list] = {
        "scale_drift": [], "resource_limit_drift": [], "node_affinity_drift": [],
        "resource_contention": [], "topology_spread_violations": [],
    }
    for svc in report.services:
        for v in svc.violations:
            categories[_CATEGORY_BY_TYPE[v.drift_type]].append({**v.model_dump(), "service_name": svc.service_name})
    return {
        "summary": {k: len(v) for k, v in categories.items()},
        "total_violations": sum(len(v) for v in categories.values()),
        "categories": categories,
    }


def get_concentration_rankings(namespace: str = "default") -> list[dict[str, Any]]:
    """Services ordered by concentration: concentrated first, then by max/total ratio."""
    graph = get_live_deployment_graph(namespace=namespace)
    ranked = [
        {
            "service_name": svc.name,
            "replica_concentration": svc.replica_concentration,
            "declared_replicas": svc.declared_replicas,
            "ready_replicas": svc.ready_replicas,
            "is_concentrated": svc.concentrated,
        }
        for svc in graph.services
    ]
    ranked.sort(key=lambda x: (x["is_concentrated"] is not True, -(x["replica_concentration"] or 0.0), x["service_name"]))
    return ranked


def get_node_risk_rankings(namespace: str = "default") -> list[dict[str, Any]]:
    """Service-level view per node: which services would lose all ready replicas if the node
    failed. Workflow-level node blast radius belongs to Component 3 and is not computed here."""
    return node_risks_from_analysis(analyze_live_topology(namespace=namespace))


def node_risks_from_analysis(analysis: TopologyAnalysis) -> list[dict[str, Any]]:
    exposed_by_node = analysis.baseline_b2.exposed_services_by_node
    pod_counts = {n.name: 0 for n in analysis.graph.nodes}
    hosted: dict[str, set] = {n.name: set() for n in analysis.graph.nodes}
    for svc in analysis.graph.services:
        for p in svc.pods:
            if p.node in pod_counts:
                pod_counts[p.node] += 1
                hosted[p.node].add(svc.name)
    rankings = [
        {
            "node_name": n.name,
            "zone": n.zone,
            "pod_count": pod_counts[n.name],
            "service_count": len(hosted[n.name]),
            "exposed_services": exposed_by_node.get(n.name, []),
            "exposed_service_count": len(exposed_by_node.get(n.name, [])),
            "risk_level": "CRITICAL" if exposed_by_node.get(n.name) else "BALANCED",
            "hosted_services": sorted(hosted[n.name]),
        }
        for n in analysis.graph.nodes
    ]
    rankings.sort(key=lambda x: (-x["exposed_service_count"], -x["pod_count"], x["node_name"]))
    return rankings


# ---------------------------------------------------------------------------------------------
# Phase 4 scenario experiments (dry-run fixtures)
# ---------------------------------------------------------------------------------------------

from experiments.placement_drift.snapshot_repository import SnapshotRepository  # noqa: E402
from experiments.placement_drift.cluster_state_freezer import ClusterStateFreezer  # noqa: E402
from experiments.placement_drift.safety_guard import SafetyGuard  # noqa: E402
from experiments.placement_drift.constants import ScenarioExecutionMode, PROTECTED_NAMESPACES  # noqa: E402
from experiments.placement_drift.drift_transition_matrix import DriftTransitionMatrix  # noqa: E402
from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator  # noqa: E402
from experiments.placement_drift.disruption_reporter import DisruptionReporter  # noqa: E402
from experiments.placement_drift.parametric_ensemble_evaluator import ParametricEnsembleEvaluator  # noqa: E402

_GLOBAL_SNAPSHOT_REPO = SnapshotRepository()


def chaos_topology(topology_source: str, namespace: str) -> DeploymentGraph | None:
    """The placements the dry-run scenarios start from.

    "live": the connected cluster's real topology (raises the usual coded error when it cannot be read, so a
    result is never quietly built on something else). "scripted": the placements written in the scenario
    code, which are made-up nodes and pods and are labelled so in every result.
    """
    if topology_source == "scripted":
        return None
    if topology_source == "live":
        graph = get_live_deployment_graph(namespace=namespace)
        if not any(pod.node != "unscheduled" for svc in graph.services for pod in svc.pods):
            raise unavailable(
                "TOPOLOGY_HAS_NO_WORKLOADS",
                "The connected cluster has no service with a placed pod in this namespace, so there is nothing "
                "to disrupt in a simulation.",
                COMPONENT,
            )
        return graph
    raise ValueError(f"topology_source must be 'live' or 'scripted', got '{topology_source}'")


def run_chaos_benchmark_pipeline(namespace: str = "default", topology_source: str = "scripted") -> dict[str, Any]:
    """Runs the 8 dry-run scenarios and returns their results and aggregate metrics."""
    orchestrator = DisruptionOrchestrator(namespace=namespace, topology=chaos_topology(topology_source, namespace))
    results = orchestrator.run_all()
    return DisruptionReporter.generate_json_payload(results, orchestrator.compute_aggregate_metrics(results))


def run_single_chaos_scenario(scenario_id: str, namespace: str = "default", topology_source: str = "scripted") -> Any:
    orchestrator = DisruptionOrchestrator(namespace=namespace, topology=chaos_topology(topology_source, namespace))
    return orchestrator.run_scenario(scenario_id)


def list_cluster_snapshots() -> list[str]:
    return _GLOBAL_SNAPSHOT_REPO.list_keys()


# Where each stored snapshot came from. A snapshot taken through one cluster connection must never be
# shown as current evidence after the connection changes, so each one records the connection fingerprint
# (a hash of the API server address and CA, never a credential) and when it was captured.
_SNAPSHOT_PROVENANCE: dict[str, dict[str, Any]] = {}


def _snapshot_unverified(reason: str) -> EvidenceError:
    return unavailable("SNAPSHOT_PROVENANCE_UNVERIFIED", reason, COMPONENT)


def _active_connection() -> str:
    """The active connection's fingerprint; refuses when no connection can vouch for stored snapshots."""
    fingerprint = discovery.get_cluster_source().connection_fingerprint()
    if fingerprint is None:
        raise _snapshot_unverified(
            "Stored snapshots cannot be shown while no cluster connection is available to confirm their origin."
        )
    if any(p["connectionFingerprint"] != fingerprint for p in _SNAPSHOT_PROVENANCE.values()):
        # They were taken through a different connection; drop them rather than show them as current.
        _GLOBAL_SNAPSHOT_REPO.clear()
        _SNAPSHOT_PROVENANCE.clear()
    return fingerprint


def list_cluster_snapshots() -> list[str]:
    """Snapshot keys whose origin is recorded and matches the active connection."""
    _active_connection()
    return [key for key in _GLOBAL_SNAPSHOT_REPO.list_keys() if key in _SNAPSHOT_PROVENANCE]


def get_cluster_snapshot(scenario_id: str, phase_tag: str) -> dict[str, Any] | None:
    """A stored snapshot, labelled as historical with its origin."""
    _active_connection()
    snap = _GLOBAL_SNAPSHOT_REPO.get(scenario_id, phase_tag)
    if not snap:
        return None
    provenance = _SNAPSHOT_PROVENANCE.get(f"{scenario_id}_{phase_tag}")
    if provenance is None:
        raise _snapshot_unverified("This snapshot has no recorded origin, so it cannot be shown as evidence.")
    return {**snap.model_dump(), "provenance": {**provenance, "historical": True}}


def freeze_cluster_state(snapshot_id: str, namespace: str = "default") -> dict[str, Any]:
    """Freezes the current live placement into a hashed snapshot, recording which connection took it."""
    fingerprint = _active_connection()
    graph = get_live_deployment_graph(namespace=namespace)
    all_pods = [
        {"pod_name": p.name, "service_name": svc.name, "node_name": p.node, "zone": p.zone, "is_ready": p.ready}
        for svc in graph.services for p in svc.pods
    ]
    snapshot = ClusterStateFreezer.freeze(
        snapshot_id=snapshot_id,
        raw_pods=all_pods,
        nodes=[{"name": n.name, "zone": n.zone} for n in graph.nodes],
        namespace=namespace,
    )
    key = _GLOBAL_SNAPSHOT_REPO.save(scenario_id=snapshot_id, phase_tag="MANUAL", snapshot=snapshot)
    provenance = {
        "capturedAt": graph.timestamp,
        "connectionFingerprint": fingerprint,
        # The fingerprint detects a change of connection; it does not prove which cluster this is.
        "identityVerified": False,
    }
    _SNAPSHOT_PROVENANCE[key] = provenance
    return {**snapshot.model_dump(), "provenance": {**provenance, "historical": False}}


def clear_cluster_snapshots() -> dict[str, Any]:
    _GLOBAL_SNAPSHOT_REPO.clear()
    _SNAPSHOT_PROVENANCE.clear()
    return {"message": "Snapshot cache cleared successfully."}


def get_chaos_safety_status() -> dict[str, Any]:
    """Safety status; node count is None when no cluster is reachable."""
    try:
        nodes_count: int | None = len(get_live_deployment_graph().nodes)
    except ClusterUnavailableError:
        nodes_count = None
    return {
        "execution_mode": ScenarioExecutionMode.DRY_RUN_SIMULATION.value,
        "protected_namespaces": sorted(PROTECTED_NAMESPACES),
        "cluster_nodes_detected": nodes_count,
        "multi_node_anti_affinity_feasible": None if nodes_count is None else nodes_count >= 2,
        "safety_guard_enforced": True,
    }


def validate_chaos_action(namespace: str = "default", service_name: str | None = None) -> dict[str, Any]:
    guard = SafetyGuard(allow_default_namespace=True)
    try:
        guard.validate_namespace_safety(namespace)
        graph = get_live_deployment_graph(namespace=namespace)
        guard.validate_preflight_nodes([{"name": n.name} for n in graph.nodes])
        valid, reason = True, "Target namespace and cluster topology satisfy all pre-flight safety checks."
    except Exception as exc:
        valid, reason = False, str(exc)
    return {"namespace": namespace, "service_name": service_name, "action_allowed": valid, "reason": reason}


def get_scenario_latex_table(scenario_id: str) -> dict[str, Any] | None:
    repo = _GLOBAL_SNAPSHOT_REPO
    result = DisruptionOrchestrator(repository=repo).run_scenario(scenario_id)
    if result is None:
        return None
    t0, t1 = repo.get(scenario_id, "T0"), repo.get(scenario_id, "T1")
    if not t0 or not t1:
        return None
    nodes, matrix, _ = DriftTransitionMatrix.build_matrix(t0, t1)
    return {
        "scenario_id": scenario_id,
        "execution_mode": result.execution_mode.value,
        "nodes": nodes,
        "latex_table": DriftTransitionMatrix.format_latex_table(nodes, matrix),
        "d_rate": result.d_rate,
        "delta_e_rate": result.delta_e_rate,
    }


def run_parametric_ensemble_pipeline() -> dict[str, Any]:
    return ParametricEnsembleEvaluator(repository=_GLOBAL_SNAPSHOT_REPO).run_ensemble().model_dump()


def get_parametric_ensemble_markdown_report() -> str:
    metrics = ParametricEnsembleEvaluator(repository=_GLOBAL_SNAPSHOT_REPO).run_ensemble()
    return ParametricEnsembleEvaluator.generate_markdown_report(metrics)


# ---------------------------------------------------------------------------------------------
# Live KinD experiments (stored results; reading them needs no cluster)
# ---------------------------------------------------------------------------------------------

def _example_run(results_dir: Path, scenario_id: str, config: str) -> dict[str, Any] | None:
    """Pod placement at T0 and T1 of the first successful stored run of one condition."""
    for path in sorted(results_dir.glob(f"{scenario_id}__{config}__run*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("failed"):
            continue
        pods = lambda snap: {  # noqa: E731
            svc: [{"name": p[0], "node": p[1], "ready": p[2]} for p in info["pods"]] for svc, info in snap.items()
        }
        # Stored by the run (placement_metrics.concentration_ratio); None when a service had no ready replica.
        concentration = lambda snap: {  # noqa: E731
            svc: {"ratio": info["replica_concentration"], "ready_replicas": info["ready_replicas"]}
            for svc, info in snap.items()
        }
        return {
            "repetition": run["repetition"],
            "action": run["action"],
            "stable_after_disruption": run["stable_after_disruption"],
            "target_status_t0": run["target_status_t0"],
            "target_status_t1": run["target_status_t1"],
            "t0": pods(run["t0"]),
            "t1": pods(run["t1"]),
            "concentration_t0": concentration(run["t0"]),
            "concentration_t1": concentration(run["t1"]),
        }
    return None


def _live_evidence_note(workload: str) -> tuple[str, list[str]]:
    """What the stored runs of one workload ran on, and the services they observed."""
    if workload == "bookinfo":
        from experiments.placement_drift.live import bookinfo_workloads as bi
        services = list(bi.SERVICES)
        return (
            f"Observed on a local KinD cluster (nodes are containers on one host) with the team's Bookinfo benchmark "
            f"(v1, images {bi.IMAGE_TAG}) in its own namespace {bi.NAMESPACE}: {len(services)} services x {bi.REPLICAS} "
            f"replicas, requests {bi.REQUESTS['cpu']}/{bi.REQUESTS['memory']}. Disruptions target reviews; "
            f"all services are observed.",
            services,
        )
    from experiments.placement_drift.live.generate_workloads import IMAGE, REPLICAS, SERVICES
    from experiments.placement_drift.live.scenarios import TARGET_SERVICE
    return (
        f"Observed on a local KinD cluster (nodes are containers on one host) with stand-in workloads: "
        f"{len(SERVICES)} services x {REPLICAS} replicas running {IMAGE}. "
        f"Disruptions target {TARGET_SERVICE}; all services are observed.",
        list(SERVICES),
    )


def _live_results_dir(workload: str) -> Path:
    """Stored runs of one workload (read at call time, so the folders can be swapped in tests)."""
    return LIVE_EXPERIMENT_BOOKINFO_DIR if workload == "bookinfo" else LIVE_EXPERIMENT_RESULTS_DIR


def available_live_experiment_workloads() -> list[str]:
    """Workloads whose live runs have a stored summary."""
    return [name for name in ("standin", "bookinfo") if (_live_results_dir(name) / "summary.json").exists()]


def get_live_experiment_results(results_dir: Path | None = None, workload: str = "standin") -> dict[str, Any] | None:
    """Summary of the stored live placement-drift runs of one workload, or None if no summary exists.

    The runs were executed by experiments/placement_drift/run_live_experiments.py; this only reads
    their summary.json, so the values are observations, not recomputed or simulated.
    """
    from experiments.placement_drift.live.scenarios import SCENARIOS_BY_ID
    from experiments.placement_drift.live.workloads import WORKLOADS

    if workload not in WORKLOADS:
        raise ValueError(f"Unknown workload '{workload}'; expected one of {sorted(WORKLOADS)}.")
    results_dir = results_dir or _live_results_dir(workload)
    summary_file = results_dir / "summary.json"
    if not summary_file.exists():
        return None
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    conditions = [
        {
            **c,
            "scenario_name": SCENARIOS_BY_ID[c["scenario_id"]].name if c["scenario_id"] in SCENARIOS_BY_ID else c["scenario_id"],
            "example_run": _example_run(results_dir, c["scenario_id"], c["config"]),
        }
        for c in summary["conditions"]
    ]
    try:
        source = str(results_dir.relative_to(PROJECT_ROOT))
    except ValueError:
        source = results_dir.name
    note, services = _live_evidence_note(workload)
    return {
        "execution_mode": "LIVE_KIND_CLUSTER",
        "is_experimental_evidence": True,
        "workload": workload,
        "available_workloads": available_live_experiment_workloads(),
        "evidence_note": note,
        "results_source": source.replace("\\", "/"),
        "target_service": WORKLOADS[workload].target_service,
        "services": services,
        "total_runs": summary["total_runs"],
        "failed_runs": sum(c["failed_runs"] for c in conditions),
        "conditions": conditions,
    }


if __name__ == "__main__":
    analysis = analyze_live_topology()
    print(f"Worker nodes: {len(analysis.graph.nodes)}; services: {len(analysis.graph.services)}")
