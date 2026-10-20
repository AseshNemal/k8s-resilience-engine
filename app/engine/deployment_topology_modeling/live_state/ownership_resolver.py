"""Resolves Service -> Pod -> ReplicaSet -> Deployment (Study Guide section 9 Step 3).

Two separate relations are resolved, never guessed from names:
1. Service membership: a pod belongs to a Service when the Service's label selector matches
   the pod's labels - the same rule Kubernetes uses to build the Service's endpoints.
2. Ownership: pod -> ReplicaSet via the pod's controller ownerReference UID, and
   ReplicaSet -> Deployment via the ReplicaSet's controller ownerReference UID.

A selected pod that is not owned by a Deployment (StatefulSet, DaemonSet, bare pod) still
belongs to the Service, because it serves the Service's traffic; it is listed in
`podsWithoutDeploymentOwner` so the gap stays visible.
"""
from typing import Any
from urllib.parse import quote

from app.engine.deployment_topology_modeling.live_state.cluster_models import (
    ClusterDeployment,
    ClusterPod,
)
from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import TERMINAL_POD_PHASES
from app.integrations.kubernetes.client import KubernetesApiSource


def _selector_matches(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return all(labels.get(key) == value for key, value in selector.items())


def _controller_owner(obj: dict[str, Any]) -> dict[str, Any] | None:
    owners = (obj.get("metadata", {}) or {}).get("ownerReferences") or []
    return next((ref for ref in owners if ref.get("controller")), None)


class _OwnerIndex:
    """pod -> ReplicaSet -> Deployment lookups by controller ownerReference UID."""

    def __init__(self, replica_sets: list[dict[str, Any]], deployments: list[dict[str, Any]]):
        deployment_by_uid = {d["metadata"]["uid"]: d for d in deployments if d.get("metadata", {}).get("uid")}
        self.deployment_of_rs_uid: dict[str, dict[str, Any]] = {}
        self.rs_uid_by_name: dict[str, str] = {}
        for rs in replica_sets:
            metadata = rs.get("metadata", {}) or {}
            if not metadata.get("uid"):
                continue
            self.rs_uid_by_name[metadata["name"]] = metadata["uid"]
            owner = _controller_owner(rs)
            if owner and owner.get("kind") == "Deployment" and owner.get("uid") in deployment_by_uid:
                self.deployment_of_rs_uid[metadata["uid"]] = deployment_by_uid[owner["uid"]]

    def deployment_of(self, pod: ClusterPod) -> dict[str, Any] | None:
        if pod.owner_kind != "ReplicaSet":
            return None
        rs_uid = pod.owner_uid or self.rs_uid_by_name.get(pod.owner_name or "")
        return self.deployment_of_rs_uid.get(rs_uid) if rs_uid else None


def _pod_record(pod: ClusterPod, deployment: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "name": pod.name,
        "node": pod.node,
        "ready": pod.ready,
        "phase": pod.phase,
        "restartCount": pod.restart_count,
        "namespace": pod.namespace,
        "ownerKind": pod.owner_kind,
        "ownerName": pod.owner_name,
        "ownerUid": pod.owner_uid,
        "deploymentName": deployment["metadata"]["name"] if deployment else None,
    }


def _template_labels(deployment: dict[str, Any]) -> dict[str, str]:
    template = (deployment.get("spec", {}) or {}).get("template", {}) or {}
    return (template.get("metadata", {}) or {}).get("labels") or {}


def _declared_replicas(deployment: dict[str, Any]) -> int:
    replicas = (deployment.get("spec") or {}).get("replicas")
    return 1 if replicas is None else replicas


def _resolve_service(
    service: dict[str, Any],
    selector: dict[str, str],
    live_pods: list[ClusterPod],
    owners: _OwnerIndex,
    deployments: list[dict[str, Any]],
    namespace: str,
) -> dict[str, Any]:
    name = service["metadata"]["name"]
    selected = sorted((p for p in live_pods if _selector_matches(selector, p.labels)), key=lambda p: p.name)
    owning: dict[str, dict[str, Any]] = {}
    pod_records = []
    for pod in selected:
        deployment = owners.deployment_of(pod)
        if deployment is not None:
            owning[deployment["metadata"]["name"]] = deployment
        pod_records.append(_pod_record(pod, deployment))

    # Deployments whose pod template the selector targets count too, so a Deployment that
    # currently has zero pods (scaled down, crash-looping) still contributes declared replicas.
    for deployment in deployments:
        if _selector_matches(selector, _template_labels(deployment)):
            owning.setdefault(deployment["metadata"]["name"], deployment)

    return {
        "name": name,
        "namespace": namespace,
        "selector": dict(selector),
        "declaredReplicas": sum(_declared_replicas(d) for d in owning.values()),
        "readyReplicas": sum(1 for r in pod_records if r["ready"]),
        "pods": pod_records,
        "deploymentNames": sorted(owning),
        "podsWithoutDeploymentOwner": [r["name"] for r in pod_records if r["deploymentName"] is None],
    }


def resolve_service_ownership(
    pods: list[ClusterPod],
    services: list[dict[str, Any]],
    replica_sets: list[dict[str, Any]],
    deployments: list[dict[str, Any]],
    namespace: str = "default",
) -> dict[str, dict[str, Any]]:
    """Pure resolution over Kubernetes JSON objects of one namespace."""
    owners = _OwnerIndex(replica_sets, deployments)
    live_pods = [p for p in pods if p.namespace == namespace and p.phase not in TERMINAL_POD_PHASES]

    result: dict[str, dict[str, Any]] = {}
    for service in services:
        selector = (service.get("spec", {}) or {}).get("selector") or {}
        if not selector:
            # A Service without a selector has manually managed endpoints; it maps to no pods.
            continue
        name = service["metadata"]["name"]
        result[name] = _resolve_service(service, selector, live_pods, owners, deployments, namespace)
    return result


def resolve_deployment_ownership(
    pods: list[ClusterPod], namespace: str = "default", source: KubernetesApiSource | None = None
) -> dict[str, dict[str, Any]]:
    """Fetches Services, ReplicaSets and Deployments of the namespace and resolves ownership.

    Reads through the integration's REST client (no Kubernetes client package), so an unusable
    connection raises a ClusterAccessError subclass and nothing is guessed.
    """
    source = source or KubernetesApiSource()
    base = f"/namespaces/{quote(namespace, safe='')}"
    return resolve_service_ownership(
        pods=pods,
        services=source.list_objects("services", f"/api/v1{base}/services"),
        replica_sets=source.list_objects("replicasets", f"/apis/apps/v1{base}/replicasets"),
        deployments=source.list_objects("deployments", f"/apis/apps/v1{base}/deployments"),
        namespace=namespace,
    )


# ---------------------------------------------------------------------------------------------
# App-label attribution used by discovery.py (the extension's live discovery)
#
# This is the earlier, simpler rule and it is kept next to the selector-based resolver above so that
# discovery.py keeps working unchanged: a pod belongs to the Deployment whose name equals the pod's
# ``app`` label. Pods that match nothing are reported, never guessed at. Moving discovery over to
# resolve_service_ownership (Service selectors) is the planned follow-up and would fix applications
# whose Deployments are named differently from their ``app`` label, such as Istio Bookinfo.
# ---------------------------------------------------------------------------------------------

def resolve_deployment_ownership_by_app_label(
    pods: list[ClusterPod],
    deployments: list[ClusterDeployment],
) -> dict[str, dict[str, Any]]:
    """Attribute pods to deployments from already-fetched cluster data (no API calls here).

    A pod with no assigned node is kept apart in ``unscheduledPods`` instead of being given a
    placeholder node, and a terminating pod is never counted as ready.
    """
    deployment_map: dict[str, dict[str, Any]] = {
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


def find_unattributed_pods(pods: list[ClusterPod], deployments: list[ClusterDeployment]) -> list[ClusterPod]:
    """Pods that the ``app``-label rule did not attribute to any deployment."""
    names = {dep.name for dep in deployments}
    return [pod for pod in pods if not pod.app_label or pod.app_label not in names]


def related_deployments(pod: ClusterPod, deployments: list[ClusterDeployment]) -> list[str]:
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
