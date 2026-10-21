"""Scores the topology model against a stored, manually inspected cluster snapshot.

Study Guide section 11 defines the references used here:
- pod -> node:      `kubectl get pods -o wide`                       (pod-placement.txt)
- service -> pod:   the Service's endpoints as reported by kubectl    (endpoints-before.txt)
- exposure:         observed service outcome after a real node failure
                    (<failure dir>/service-result.txt + nodes-after-detection.txt)

The model is fed only pods.json, nodes.json and the Service manifest, and its output is compared
with the kubectl outputs above. Owner resolution accuracy is not scored: the snapshot holds no
ReplicaSet/Deployment objects, and scoring ownerReferences against themselves would be circular.
"""
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.engine.deployment_topology_modeling import placement_metrics as pm
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import ModelAccuracyCertificate
from app.engine.deployment_topology_modeling.drift_analysis.ground_truth_validator import _map_accuracy
from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import (
    nodes_from_objects,
    pods_from_objects,
)
from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import analyze_replica_concentration
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import resolve_service_ownership

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KIND_CONCENTRATED_BASELINE = PROJECT_ROOT / "experiments" / "single-node-failure" / "concentrated-baseline"


def _table_rows(text: str) -> list[dict[str, str]]:
    """Parses kubectl's fixed-width table output by header column positions.

    Header names may contain single spaces ("NOMINATED NODE"), so columns are located by their
    start offset in the header rather than by splitting on whitespace.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    columns = [(m.group(), m.start()) for m in re.finditer(r"\S+(?: \S+)*", lines[0])]
    rows = []
    for line in lines[1:]:
        row = {}
        for i, (name, start) in enumerate(columns):
            end = columns[i + 1][1] if i + 1 < len(columns) else None
            row[name] = line[start:end].strip()
        rows.append(row)
    return rows


@dataclass
class FailureObservation:
    failed_nodes: list[str]
    service_available_after: bool


@dataclass
class SnapshotEvaluation:
    certificate: ModelAccuracyCertificate
    pods_evaluated: int
    services_evaluated: int
    failure_experiments: int
    predicted_pod_node: dict[str, str] = field(default_factory=dict)
    predicted_pod_service: dict[str, str] = field(default_factory=dict)
    exposure_checks: list[dict] = field(default_factory=list)


def _observed_failures(snapshot_dir: Path) -> list[FailureObservation]:
    observations = []
    for result_file in sorted(snapshot_dir.glob("*/service-result.txt")):
        nodes_file = result_file.parent / "nodes-after-detection.txt"
        if not nodes_file.exists():
            continue
        failed = [r["NAME"] for r in _table_rows(nodes_file.read_text(encoding="utf-8")) if r.get("STATUS") == "NotReady"]
        outcome = result_file.read_text(encoding="utf-8").strip()
        observations.append(FailureObservation(failed, outcome == "SERVICE_AVAILABLE"))
    return observations


def evaluate_snapshot(snapshot_dir: Path = KIND_CONCENTRATED_BASELINE) -> SnapshotEvaluation:
    start = time.perf_counter()
    pod_objects = json.loads((snapshot_dir / "pods.json").read_text(encoding="utf-8"))["items"]
    node_objects = json.loads((snapshot_dir / "nodes.json").read_text(encoding="utf-8"))["items"]
    services = [
        doc for path in sorted(snapshot_dir.glob("*-service.yaml"))
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(doc, dict)
    ]
    namespace = pod_objects[0]["metadata"]["namespace"] if pod_objects else "default"

    nodes = nodes_from_objects(node_objects, pod_objects)
    pods = pods_from_objects(pod_objects, namespace)
    workloads = resolve_service_ownership(pods, services, replica_sets=[], deployments=[], namespace=namespace)
    analyzed = analyze_replica_concentration(workloads, {n.name: n.ready for n in nodes})
    runtime_ms = round((time.perf_counter() - start) * 1000.0, 2)

    predicted_pod_node = {p.name: p.node for p in pods if p.node}
    predicted_pod_service = {pod["name"]: svc["name"] for svc in analyzed for pod in svc["pods"]}

    wide = _table_rows((snapshot_dir / "pod-placement.txt").read_text(encoding="utf-8"))
    truth_pod_node = {r["NAME"]: r["NODE"] for r in wide}
    ip_to_pod = {r["IP"]: r["NAME"] for r in wide}
    truth_pod_service = {}
    for row in _table_rows((snapshot_dir / "endpoints-before.txt").read_text(encoding="utf-8")):
        service_name = row["NAME"].rsplit("-", 1)[0]  # EndpointSlice name = <service>-<suffix>
        for ip in row.get("ENDPOINTS", "").split(","):
            if ip in ip_to_pod:
                truth_pod_service[ip_to_pod[ip]] = service_name

    # Each observed node failure is one labelled case: predicted SA(s, F) vs observed availability.
    exposure_checks = []
    for obs in _observed_failures(snapshot_dir):
        for svc in analyzed:
            per_node = pm.ready_replicas_per_node(svc["pods"])
            predicted_survives = any(n not in obs.failed_nodes for n in per_node)
            exposure_checks.append({
                "service": svc["name"],
                "failed_nodes": obs.failed_nodes,
                "predicted_available": predicted_survives,
                "observed_available": obs.service_available_after,
            })
    exposure_acc = (
        sum(c["predicted_available"] == c["observed_available"] for c in exposure_checks) / len(exposure_checks)
        if exposure_checks else None
    )

    certificate = ModelAccuracyCertificate(
        service_pod_mapping_accuracy=_map_accuracy(predicted_pod_service, truth_pod_service),
        pod_node_mapping_accuracy=_map_accuracy(predicted_pod_node, truth_pod_node),
        owner_resolution_accuracy=None,
        # With the node as failure domain, concentration and exposure are the same condition;
        # the observed failure outcome is the label for both.
        concentration_detection_accuracy=exposure_acc,
        exposure_detection_accuracy=exposure_acc,
        runtime_overhead_ms=runtime_ms,
        is_statistically_sound=False,
    )
    return SnapshotEvaluation(
        certificate=certificate,
        pods_evaluated=len(truth_pod_node),
        services_evaluated=len(analyzed),
        failure_experiments=len(_observed_failures(snapshot_dir)),
        predicted_pod_node=predicted_pod_node,
        predicted_pod_service=predicted_pod_service,
        exposure_checks=exposure_checks,
    )
