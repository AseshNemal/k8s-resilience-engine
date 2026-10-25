"""Scores the topology model against a stored, manually inspected cluster snapshot.

Study Guide section 11 defines the references used here:
- pod -> node:      `kubectl get pods -o wide`                       (pod-placement.txt)
- service -> pod:   the Service's endpoints as reported by kubectl    (endpoints-before.txt)
- exposure:         observed service outcome after a real node failure
                    (<failure dir>/service-result.txt + nodes-after-detection.txt)

The model is fed only pods.json, nodes.json and the Service manifest, and its output is compared
with the kubectl outputs above. Owner resolution is scored only when a recording also holds
replicasets.json, deployments.json and owner-truth.txt: the truth there is the API server's own
label-selector query per Deployment (`kubectl get pods -l <selector>`), not the ownerReferences the
model walks, so the two are independent.

A recording may hold one service (service-result.txt is a single word for it) or several
(one "<service> SERVICE_AVAILABLE|SERVICE_UNAVAILABLE" line each). Several recordings, each with
its own before-snapshot and one node failure, are pooled by evaluate_recordings().
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
GROUND_TRUTH_ROOT = PROJECT_ROOT / "experiments" / "single-node-failure"
KIND_CONCENTRATED_BASELINE = GROUND_TRUTH_ROOT / "concentrated-baseline"


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
    # Observed availability per service; the key None means "the recording's only service".
    available_after: dict[str | None, bool]

    def observed(self, service: str) -> bool | None:
        return self.available_after.get(service, self.available_after.get(None))


@dataclass
class SnapshotEvaluation:
    certificate: ModelAccuracyCertificate
    pods_evaluated: int
    services_evaluated: int
    failure_experiments: int
    predicted_pod_node: dict[str, str] = field(default_factory=dict)
    predicted_pod_service: dict[str, str] = field(default_factory=dict)
    exposure_checks: list[dict] = field(default_factory=list)
    # (correct, compared) per measure, so several recordings can be pooled by counts, not by averaging ratios.
    counts: dict[str, tuple[int, int]] = field(default_factory=dict)


def _map_counts(predicted: dict[str, str], truth: dict[str, str]) -> tuple[int, int]:
    """Same rule as _map_accuracy (union of keys; a missing or invented prediction is an error), as counts."""
    # Using keys from both sides means a pod the model missed, or a pod it made up, both count as wrong.
    keys = set(truth) | set(predicted)
    return sum(predicted.get(k) == truth.get(k) for k in keys), len(keys)


def _parse_service_result(text: str) -> dict[str | None, bool]:
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if len(lines) == 1 and len(lines[0]) == 1:
        return {None: lines[0][0] == "SERVICE_AVAILABLE"}
    return {parts[0]: parts[1] == "SERVICE_AVAILABLE" for parts in lines if len(parts) == 2}


def _observed_failures(snapshot_dir: Path) -> list[FailureObservation]:
    observations = []
    for result_file in sorted(snapshot_dir.glob("*/service-result.txt")):
        nodes_file = result_file.parent / "nodes-after-detection.txt"
        if not nodes_file.exists():
            continue
        failed = [r["NAME"] for r in _table_rows(nodes_file.read_text(encoding="utf-8")) if r.get("STATUS") == "NotReady"]
        observations.append(FailureObservation(failed, _parse_service_result(result_file.read_text(encoding="utf-8"))))
    return observations


def _owner_truth(snapshot_dir: Path) -> dict[str, str] | None:
    """pod -> Deployment from the API server's selector query per Deployment, or None if not recorded."""
    path = snapshot_dir / "owner-truth.txt"
    if not path.exists():
        return None
    return {pod: deployment for deployment, pod in
            (line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())}


def _exposure_checks(analyzed: list[dict], observations: list[FailureObservation]) -> list[dict]:
    """One labelled case per service and observed node failure: predicted SA(s, F) against observed availability."""
    checks = []
    for obs in observations:
        for svc in analyzed:
            observed = obs.observed(svc["name"])
            if observed is None:
                continue  # no recorded outcome for this service: not a labelled case
            per_node = pm.ready_replicas_per_node(svc["pods"])
            # Prediction made from the "before" snapshot only: does a ready copy sit on a node that did not fail?
            checks.append({
                "service": svc["name"],
                "failed_nodes": obs.failed_nodes,
                "predicted_available": any(n not in obs.failed_nodes for n in per_node),
                "observed_available": observed,
            })
    return checks


def _items(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["items"] if path.exists() else []


def evaluate_snapshot(snapshot_dir: Path = KIND_CONCENTRATED_BASELINE) -> SnapshotEvaluation:
    start = time.perf_counter()
    # 1. The model's input: the raw Kubernetes objects saved before the node was stopped.
    pod_objects = json.loads((snapshot_dir / "pods.json").read_text(encoding="utf-8"))["items"]
    node_objects = json.loads((snapshot_dir / "nodes.json").read_text(encoding="utf-8"))["items"]
    services = [
        doc for path in sorted(snapshot_dir.glob("*-service.yaml"))
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(doc, dict)
    ]
    namespace = pod_objects[0]["metadata"]["namespace"] if pod_objects else "default"
    replica_sets = _items(snapshot_dir / "replicasets.json")
    deployments = _items(snapshot_dir / "deployments.json")

    # 2. Run the same code the live tool runs.
    nodes = nodes_from_objects(node_objects, pod_objects)
    pods = pods_from_objects(pod_objects, namespace)
    workloads = resolve_service_ownership(pods, services, replica_sets=replica_sets, deployments=deployments, namespace=namespace)
    analyzed = analyze_replica_concentration(workloads, {n.name: n.ready for n in nodes})
    runtime_ms = round((time.perf_counter() - start) * 1000.0, 2)

    # 3. The model's answers.
    predicted_pod_node = {p.name: p.node for p in pods if p.node}
    predicted_pod_service = {pod["name"]: svc["name"] for svc in analyzed for pod in svc["pods"]}
    predicted_owner = {r["name"]: r["deploymentName"] for w in workloads.values() for r in w["pods"] if r.get("deploymentName")}
    truth_owner = _owner_truth(snapshot_dir) if replica_sets and deployments else None

    # 4. The answer sheet, taken from separate kubectl outputs the model never reads.
    wide = _table_rows((snapshot_dir / "pod-placement.txt").read_text(encoding="utf-8"))
    truth_pod_node = {r["NAME"]: r["NODE"] for r in wide}
    ip_to_pod = {r["IP"]: r["NAME"] for r in wide}
    truth_pod_service = {}
    # EndpointSlices list pod IPs, so each IP is turned back into a pod name with the -o wide table.
    for row in _table_rows((snapshot_dir / "endpoints-before.txt").read_text(encoding="utf-8")):
        service_name = row["NAME"].rsplit("-", 1)[0]  # EndpointSlice name = <service>-<suffix>
        for ip in row.get("ENDPOINTS", "").split(","):
            if ip in ip_to_pod:
                truth_pod_service[ip_to_pod[ip]] = service_name

    # Each observed node failure is one labelled case: predicted SA(s, F) vs observed availability.
    observations = _observed_failures(snapshot_dir)
    exposure_checks = _exposure_checks(analyzed, observations)
    exposure = (sum(c["predicted_available"] == c["observed_available"] for c in exposure_checks), len(exposure_checks))

    # 5. Mark the answers: (correct, compared) for each kind of check.
    counts = {
        "service_pod": _map_counts(predicted_pod_service, truth_pod_service),
        "pod_node": _map_counts(predicted_pod_node, truth_pod_node),
        "exposure": exposure,
    }
    if truth_owner is not None:
        counts["owner"] = _map_counts(predicted_owner, truth_owner)
    return SnapshotEvaluation(
        certificate=_certificate(counts, runtime_ms),
        pods_evaluated=len(truth_pod_node),
        services_evaluated=len(analyzed),
        failure_experiments=len(observations),
        predicted_pod_node=predicted_pod_node,
        predicted_pod_service=predicted_pod_service,
        exposure_checks=exposure_checks,
        counts=counts,
    )


def _ratio(count: tuple[int, int] | None) -> float | None:
    return None if not count or count[1] == 0 else count[0] / count[1]


def _certificate(counts: dict[str, tuple[int, int]], runtime_ms: float | None) -> ModelAccuracyCertificate:
    exposure_acc = _ratio(counts.get("exposure"))
    return ModelAccuracyCertificate(
        service_pod_mapping_accuracy=_ratio(counts.get("service_pod")),
        pod_node_mapping_accuracy=_ratio(counts.get("pod_node")),
        owner_resolution_accuracy=_ratio(counts.get("owner")),
        # With the node as failure domain, concentration and exposure are the same condition;
        # the observed failure outcome is the label for both.
        concentration_detection_accuracy=exposure_acc,
        exposure_detection_accuracy=exposure_acc,
        runtime_overhead_ms=runtime_ms,
        is_statistically_sound=False,
    )


def recording_dirs(root: Path = GROUND_TRUTH_ROOT) -> list[Path]:
    """Every stored recording under root: a folder with pods.json and at least one node-failure folder."""
    return sorted(p.parent for p in root.rglob("pods.json") if any(p.parent.glob("*/service-result.txt")))


@dataclass
class PooledEvaluation:
    certificate: ModelAccuracyCertificate
    counts: dict[str, tuple[int, int]]
    recordings: list[dict]
    exposure_checks: list[dict]

    @property
    def confusion(self) -> dict[str, int]:
        """Exposure predictions against the observed outcome after each real node failure."""
        out = {"down_predicted_down": 0, "down_predicted_up": 0, "up_predicted_up": 0, "up_predicted_down": 0}
        for c in self.exposure_checks:
            observed = "up" if c["observed_available"] else "down"
            predicted = "up" if c["predicted_available"] else "down"
            out[f"{observed}_predicted_{predicted}"] += 1
        return out


def evaluate_recordings(dirs: list[Path]) -> PooledEvaluation:
    """Pools the recordings by counts: each measure is (all correct) / (all compared) across them."""
    # Add the correct and compared counts of every recording, then divide once (e.g. 24 / 24),
    # instead of averaging three percentages.
    totals: dict[str, list[int]] = {}
    recordings, checks, runtimes = [], [], []
    for d in dirs:
        ev = evaluate_snapshot(d)
        for key, (correct, compared) in ev.counts.items():
            total = totals.setdefault(key, [0, 0])
            total[0] += correct
            total[1] += compared
        try:
            source = d.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            source = d.name
        recordings.append({
            "source": source, "pods": ev.pods_evaluated, "services": ev.services_evaluated,
            "failure_runs": ev.failure_experiments, "owner_scored": "owner" in ev.counts,
        })
        checks += [{**c, "source": source} for c in ev.exposure_checks]
        runtimes.append(ev.certificate.runtime_overhead_ms)
    counts = {k: (v[0], v[1]) for k, v in totals.items()}
    runtime = round(sum(runtimes) / len(runtimes), 2) if runtimes else None
    return PooledEvaluation(_certificate(counts, runtime), counts, recordings, checks)
