from typing import Any, Dict, List
from app.engine.deployment_topology_modeling import placement_metrics as pm


def analyze_replica_concentration(
    workloads: Dict[str, Dict[str, Any]],
    node_ready: Dict[str, bool | None] | None = None,
) -> List[Dict[str, Any]]:
    """Per-service concentration and single-node exposure (definitions in placement_metrics).

    A pod counts as ready only if its Ready condition is true AND its node is not reporting
    NotReady. After a node fails, Kubernetes keeps its pods' Ready condition true for a grace
    period (seen in experiments/single-node-failure/.../worker1-failure/pods.txt), so the pod
    condition alone overstates surviving replicas (proposal section 15: readiness is not
    availability). The raw pod condition is kept as `readyCondition`.
    """
    node_ready = node_ready or {}
    analyzed: List[Dict[str, Any]] = []

    for name, data in workloads.items():
        pods = []
        for p in data["pods"]:
            node = p.get("node")
            effective = bool(p.get("ready")) and node_ready.get(node) is not False
            pods.append({**p, "readyCondition": bool(p.get("ready")), "ready": effective})

        ratio = pm.concentration_ratio(pods)
        analyzed.append({
            **{k: v for k, v in data.items() if k != "pods"},
            "name": name,
            "readyReplicas": pm.ready_replica_count(pods),
            "replicaConcentration": round(ratio, 4) if ratio is not None else None,
            "concentrated": pm.is_concentrated(pods),
            "exposedNodes": pm.exposed_failure_domains(pods),
            "exposureStatus": pm.exposure_status(pods).value,
            "pods": pods,
        })

    return analyzed
