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

from kubernetes import client

from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterPod
from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import (
    TERMINAL_POD_PHASES,
    _load_kubernetes_config,
    to_json_objects,
)


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
    pods: list[ClusterPod], namespace: str = "default"
) -> dict[str, dict[str, Any]]:
    """Fetches Services, ReplicaSets and Deployments of the namespace and resolves ownership."""
    _load_kubernetes_config()
    core_v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    return resolve_service_ownership(
        pods=pods,
        services=to_json_objects(core_v1.list_namespaced_service(namespace=namespace).items),
        replica_sets=to_json_objects(apps_v1.list_namespaced_replica_set(namespace=namespace).items),
        deployments=to_json_objects(apps_v1.list_namespaced_deployment(namespace=namespace).items),
        namespace=namespace,
    )
