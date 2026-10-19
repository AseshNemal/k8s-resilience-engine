"""Reads nodes and pods from the Kubernetes API.

Parsing is done by pure functions over Kubernetes JSON objects (the shape returned by
`kubectl get ... -o json`), so the same code handles the live API, stored API snapshots
(proposal section 9.2 fallback) and tests. Only the get_* functions talk to the cluster.
"""
from typing import Any, List

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException
from kubernetes.utils.quantity import parse_quantity

from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterNode, ClusterPod

CONTROL_PLANE_LABELS = ("node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master")
TERMINAL_POD_PHASES = frozenset({"Succeeded", "Failed"})
ZONE_LABEL = "topology.kubernetes.io/zone"


def _load_kubernetes_config() -> None:
    """Use in-cluster credentials when available, otherwise use the local kubeconfig."""
    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config()


def to_json_objects(items: list[Any]) -> list[dict[str, Any]]:
    """Converts kubernetes-client model objects to their API JSON form (camelCase keys)."""
    return client.ApiClient().sanitize_for_serialization(items)


def _quantity(value: Any) -> float:
    return float(parse_quantity(value if value is not None else "0"))


def _pod_requests(pod: dict[str, Any]) -> tuple[float, float]:
    """Effective pod request = max(sum of containers, largest init container) + overhead."""
    spec = pod.get("spec", {}) or {}

    def requests_of(container: dict[str, Any]) -> dict[str, Any]:
        return ((container.get("resources") or {}).get("requests")) or {}

    containers = [requests_of(c) for c in spec.get("containers", []) or []]
    inits = [requests_of(c) for c in spec.get("initContainers", []) or []]
    overhead = spec.get("overhead") or {}
    cpu = max(sum(_quantity(r.get("cpu")) for r in containers), max((_quantity(r.get("cpu")) for r in inits), default=0.0))
    mem = max(sum(_quantity(r.get("memory")) for r in containers), max((_quantity(r.get("memory")) for r in inits), default=0.0))
    return cpu + _quantity(overhead.get("cpu")), mem + _quantity(overhead.get("memory"))


def _condition(obj: dict[str, Any], condition_type: str) -> bool | None:
    for cond in (obj.get("status", {}) or {}).get("conditions", []) or []:
        if cond.get("type") == condition_type:
            return cond.get("status") == "True"
    return None


def nodes_from_objects(node_objects: list[dict[str, Any]], pod_objects: list[dict[str, Any]]) -> List[ClusterNode]:
    """Worker nodes with remaining schedulable capacity (allocatable minus scheduled requests)."""
    requested: dict[str, tuple[float, float]] = {}
    for pod in pod_objects:
        node_name = (pod.get("spec", {}) or {}).get("nodeName")
        if not node_name or (pod.get("status", {}) or {}).get("phase") in TERMINAL_POD_PHASES:
            continue
        cpu, mem = _pod_requests(pod)
        prev_cpu, prev_mem = requested.get(node_name, (0.0, 0.0))
        requested[node_name] = (prev_cpu + cpu, prev_mem + mem)

    nodes: List[ClusterNode] = []
    for node in node_objects:
        metadata = node.get("metadata", {}) or {}
        labels = {str(k): str(v) for k, v in (metadata.get("labels") or {}).items()}
        if any(label in labels for label in CONTROL_PLANE_LABELS):
            continue
        name = metadata["name"]
        allocatable = (node.get("status", {}) or {}).get("allocatable") or {}
        used_cpu, used_mem = requested.get(name, (0.0, 0.0))
        nodes.append(ClusterNode(
            name=name,
            zone=labels.get(ZONE_LABEL),
            cpu_available=str(max(0.0, _quantity(allocatable.get("cpu")) - used_cpu)),
            memory_available=str(max(0.0, _quantity(allocatable.get("memory")) - used_mem)),
            labels=labels,
            ready=_condition(node, "Ready"),
            schedulable=not bool((node.get("spec", {}) or {}).get("unschedulable")),
        ))
    return nodes


def pods_from_objects(pod_objects: list[dict[str, Any]], namespace: str = "default") -> List[ClusterPod]:
    pods: List[ClusterPod] = []
    for pod in pod_objects:
        metadata = pod.get("metadata", {}) or {}
        status = pod.get("status", {}) or {}
        owners = metadata.get("ownerReferences") or []
        owner = next((ref for ref in owners if ref.get("controller")), owners[0] if owners else None)
        labels = {str(k): str(v) for k, v in (metadata.get("labels") or {}).items()}
        pods.append(ClusterPod(
            name=metadata["name"],
            node=(pod.get("spec", {}) or {}).get("nodeName"),
            ready=_condition(pod, "Ready") is True,
            owner_kind=owner.get("kind") if owner else None,
            owner_name=owner.get("name") if owner else None,
            app_label=labels.get("app", ""),
            labels=labels,
            namespace=metadata.get("namespace") or namespace,
            restart_count=sum(cs.get("restartCount") or 0 for cs in status.get("containerStatuses") or []),
            owner_uid=owner.get("uid") if owner else None,
            phase=status.get("phase"),
        ))
    return pods


def get_cluster_nodes() -> List[ClusterNode]:
    """Fetch worker nodes from the live cluster (control-plane nodes are excluded)."""
    _load_kubernetes_config()
    v1 = client.CoreV1Api()
    node_objects = to_json_objects(v1.list_node().items)
    pod_objects = to_json_objects(v1.list_pod_for_all_namespaces().items)
    return nodes_from_objects(node_objects, pod_objects)


def get_cluster_pods(namespace: str = "default") -> List[ClusterPod]:
    """Fetch pods of one namespace from the live cluster."""
    _load_kubernetes_config()
    v1 = client.CoreV1Api()
    return pods_from_objects(to_json_objects(v1.list_namespaced_pod(namespace=namespace).items), namespace)
