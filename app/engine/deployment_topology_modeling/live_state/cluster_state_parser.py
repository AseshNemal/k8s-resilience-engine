"""Reads nodes and pods from the Kubernetes API.

Parsing is done by pure functions over Kubernetes JSON objects (the shape returned by
`kubectl get ... -o json`), so the same code handles the live API, stored API snapshots
(proposal section 9.2 fallback) and tests. Only the get_* functions talk to the cluster.
"""
import re
from typing import Any, List
from urllib.parse import quote

from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterNode, ClusterPod
from app.integrations.kubernetes.client import KubernetesApiSource

CONTROL_PLANE_LABELS = ("node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master")
TERMINAL_POD_PHASES = frozenset({"Succeeded", "Failed"})
ZONE_LABEL = "topology.kubernetes.io/zone"


# Kubernetes resource quantities: a number plus an optional binary (Ki..Ei), decimal (k..E) or
# milli/micro/nano suffix. Replaces the kubernetes package's parse_quantity so that package is not a
# dependency; the result is the same plain number ("500m" -> 0.5, "4Gi" -> 4294967296.0).
_QUANTITY_SUFFIX = {
    "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0,
    "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
    "Ki": 2.0 ** 10, "Mi": 2.0 ** 20, "Gi": 2.0 ** 30, "Ti": 2.0 ** 40, "Pi": 2.0 ** 50, "Ei": 2.0 ** 60,
}
_QUANTITY = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]*)")


def parse_quantity(value: Any) -> float:
    """A Kubernetes quantity as a float. Raises ValueError for text that is not a quantity."""
    match = _QUANTITY.fullmatch(str(value).strip())
    if match is None or match.group(2) not in _QUANTITY_SUFFIX:
        raise ValueError(f"not a Kubernetes quantity: {value!r}")
    return float(match.group(1)) * _QUANTITY_SUFFIX[match.group(2)]


def _quantity(value: Any) -> float:
    return parse_quantity(value if value is not None else "0")


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


def _pod_owner(metadata: dict[str, Any]) -> dict[str, Any]:
    """Controller ownerReference, else the first one, else an empty dict."""
    owners = metadata.get("ownerReferences") or []
    return next((ref for ref in owners if ref.get("controller")), owners[0] if owners else {})


def _pod_from_object(pod: dict[str, Any], namespace: str) -> ClusterPod:
    metadata = pod.get("metadata", {}) or {}
    status = pod.get("status", {}) or {}
    owner = _pod_owner(metadata)
    labels = {str(k): str(v) for k, v in (metadata.get("labels") or {}).items()}
    return ClusterPod(
        name=metadata["name"],
        node=(pod.get("spec", {}) or {}).get("nodeName"),
        ready=_condition(pod, "Ready") is True,
        owner_kind=owner.get("kind"),
        owner_name=owner.get("name"),
        app_label=labels.get("app", ""),
        labels=labels,
        namespace=metadata.get("namespace") or namespace,
        restart_count=sum(cs.get("restartCount") or 0 for cs in status.get("containerStatuses") or []),
        owner_uid=owner.get("uid"),
        phase=status.get("phase"),
    )


def pods_from_objects(pod_objects: list[dict[str, Any]], namespace: str = "default") -> List[ClusterPod]:
    return [_pod_from_object(pod, namespace) for pod in pod_objects]


def get_cluster_nodes(source: KubernetesApiSource | None = None) -> List[ClusterNode]:
    """Fetch worker nodes from the live cluster (control-plane nodes are excluded).

    Reads through the integration's REST client, so a missing or unusable connection raises one of the
    ClusterAccessError subclasses instead of falling back to anything.
    """
    source = source or KubernetesApiSource()
    node_objects = source.list_objects("nodes", "/api/v1/nodes")
    pod_objects = source.list_objects("pods", "/api/v1/pods")
    return nodes_from_objects(node_objects, pod_objects)


def get_cluster_pods(namespace: str = "default", source: KubernetesApiSource | None = None) -> List[ClusterPod]:
    """Fetch pods of one namespace from the live cluster."""
    source = source or KubernetesApiSource()
    items = source.list_objects("pods", f"/api/v1/namespaces/{quote(namespace, safe='')}/pods")
    return pods_from_objects(items, namespace)
