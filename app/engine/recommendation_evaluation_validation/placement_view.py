"""Where each service's ready replicas would run after a simulated change, in the shape the Assessment draws.

It reads a (virtual) deployment dump and counts ready pods per node, exactly as the overview counts today's
placement, so the two can be laid over each other. The result is a SIMULATED target placement: the scheduler
decides where a new pod really lands, and nothing here is observed.
"""
from typing import Any


def placement_view(deployment: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """One row per service: ``{service, readyReplicas, nodes: [{node, readyPods}]}``. ``None`` when nothing was simulated."""
    if not deployment or not isinstance(deployment.get("services"), list):
        return None
    rows = []
    for service in sorted(deployment["services"], key=lambda item: item.get("name") or ""):
        per_node: dict[str, int] = {}
        for pod in service.get("pods") or []:
            if pod.get("ready") and pod.get("node"):
                per_node[pod["node"]] = per_node.get(pod["node"], 0) + 1
        rows.append({
            "service": service.get("name"),
            "readyReplicas": sum(per_node.values()),
            "nodes": [{"node": name, "readyPods": count} for name, count in sorted(per_node.items())],
        })
    return rows
