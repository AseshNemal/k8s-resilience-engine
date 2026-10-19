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


def resolve_service_ownership(
    pods: list[ClusterPod],
    services: list[dict[str, Any]],
    replica_sets: list[dict[str, Any]],
    deployments: list[dict[str, Any]],
    namespace: str = "default",
) -> dict[str, dict[str, Any]]:
    """Pure resolution over Kubernetes JSON objects of one namespace."""
    deployment_by_uid = {d["metadata"]["uid"]: d for d in deployments if d.get("metadata", {}).get("uid")}

    deployment_of_rs_uid: dict[str, dict[str, Any]] = {}
    rs_uid_by_name: dict[str, str] = {}
    for rs in replica_sets:
        metadata = rs.get("metadata", {}) or {}
        if not metadata.get("uid"):
            continue
        rs_uid_by_name[metadata["name"]] = metadata["uid"]
        owner = _controller_owner(rs)
        if owner and owner.get("kind") == "Deployment" and owner.get("uid") in deployment_by_uid:
            deployment_of_rs_uid[metadata["uid"]] = deployment_by_uid[owner["uid"]]

    def deployment_of(pod: ClusterPod) -> dict[str, Any] | None:
        if pod.owner_kind != "ReplicaSet":
            return None
        rs_uid = pod.owner_uid or rs_uid_by_name.get(pod.owner_name or "")
        return deployment_of_rs_uid.get(rs_uid) if rs_uid else None

    live_pods = [p for p in pods if p.namespace == namespace and p.phase not in TERMINAL_POD_PHASES]

    result: dict[str, dict[str, Any]] = {}
    for service in services:
        name = service["metadata"]["name"]
        selector = (service.get("spec", {}) or {}).get("selector") or {}
        if not selector:
            # A Service without a selector has manually managed endpoints; it maps to no pods.
            continue

        selected = sorted(
            (p for p in live_pods if _selector_matches(selector, p.labels)), key=lambda p: p.name
        )
        owning = {}
        pod_records = []
        for pod in selected:
            deployment = deployment_of(pod)
            if deployment is not None:
                owning[deployment["metadata"]["name"]] = deployment
            pod_records.append({
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
            })

        # Deployments whose pod template the selector targets count too, so a Deployment that
        # currently has zero pods (scaled down, crash-looping) still contributes declared replicas.
        for deployment in deployments:
            template_labels = ((deployment.get("spec", {}) or {}).get("template", {}) or {}).get("metadata", {}) or {}
            if _selector_matches(selector, template_labels.get("labels") or {}):
                owning.setdefault(deployment["metadata"]["name"], deployment)

        declared = sum(
            d["spec"]["replicas"] if (d.get("spec") or {}).get("replicas") is not None else 1
            for d in owning.values()
        )
        result[name] = {
            "name": name,
            "namespace": namespace,
            "selector": dict(selector),
            "declaredReplicas": declared,
            "readyReplicas": sum(1 for r in pod_records if r["ready"]),
            "pods": pod_records,
            "deploymentNames": sorted(owning),
            "podsWithoutDeploymentOwner": [r["name"] for r in pod_records if r["deploymentName"] is None],
        }
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
