from typing import Any, Dict, List

from app.engine.deployment_topology_modeling.live_state.cluster_models import (
    ClusterDeployment,
    ClusterPod,
)


def resolve_deployment_ownership(
    pods: List[ClusterPod],
    deployments: List[ClusterDeployment],
) -> Dict[str, Dict[str, Any]]:
    """
    Attribute pods to deployments using already-fetched cluster data (no API calls here).

    Phase 1 attribution rule, unchanged from before: a pod belongs to the deployment whose name equals
    the pod's ``app`` label. Pods that match nothing are not guessed at; callers find them with
    ``find_unattributed_pods`` and report them. A pod with no assigned node is kept apart in
    ``unscheduledPods`` instead of being given a placeholder node.
    """
    deployment_map: Dict[str, Dict[str, Any]] = {
        dep.name: {
            "name": dep.name,
            "declaredReplicas": dep.declared_replicas,
            "readyReplicas": 0,
            "pods": [],
            "unscheduledPods": [],
        }
        for dep in deployments
    }

    for pod in pods:
        workload = deployment_map.get(pod.app_label) if pod.app_label else None
        if workload is None:
            continue
        if pod.node is None:
            workload["unscheduledPods"].append(pod.name)
            continue
        is_ready = pod.ready and not pod.terminating
        workload["pods"].append({
            "name": pod.name,
            "node": pod.node,
            "ready": is_ready,
            "restart_count": pod.restart_count,
        })
        if is_ready:
            workload["readyReplicas"] += 1

    return deployment_map


def find_unattributed_pods(pods: List[ClusterPod], deployments: List[ClusterDeployment]) -> List[ClusterPod]:
    """Pods that the ``app``-label rule did not attribute to any deployment."""
    names = {dep.name for dep in deployments}
    return [pod for pod in pods if not pod.app_label or pod.app_label not in names]


def related_deployments(pod: ClusterPod, deployments: List[ClusterDeployment]) -> List[str]:
    """Deployments an unattributed pod might belong to, judged by name only.

    Deliberately conservative: a match makes that deployment's placement evidence incomplete. It is not
    proof of ownership.
    """
    related = []
    for dep in deployments:
        prefix = f"{dep.name}-"
        if (pod.owner_name or "").startswith(prefix) or pod.name.startswith(prefix):
            related.append(dep.name)
    return related
