"""The Failure lab: stop the workers the person chooses, together, hold them down, and record what the application did.

A lab run is an experiment, not a proof: no change is applied, there is no candidate to select, and no pass or fail is invented. What it
reports is what was measured (see ``monitoring/``) and, beside it, the simple placement rule applied to the nodes chosen: a service is
DOWN when every ready replica ran on the stopped nodes, DEGRADED when some did. That rule is the engine's, written here for a SET of
nodes because Component 3's predictor takes one failed node. It is a statement about placement, not a prediction of the workflow.

Pure functions over a ``DeploymentGraph``; nothing here touches a cluster.
"""

import hashlib
from collections.abc import Sequence
from typing import Any

from app.shared.schemas.deployment import DeploymentGraph


def group_label(nodes: Sequence[str]) -> str:
    """The name an experiment of several workers is stored under (the worker column holds up to 100 characters)."""
    joined = "+".join(sorted(nodes))
    return joined if len(joined) <= 90 else f"lab-{hashlib.sha256(joined.encode()).hexdigest()[:12]}"


def placement_impact(graph: DeploymentGraph, nodes: Sequence[str]) -> dict[str, Any]:
    """What stopping ``nodes`` does to every service, from where its ready replicas are. Services with no ready replica are not blamed."""
    stopped = set(nodes)
    services = []
    for service in graph.services:
        ready = [pod for pod in service.pods if pod.ready]
        if not ready:
            services.append({"service": service.name, "ready": 0, "lost": 0, "left": 0, "state": "none"})
            continue
        lost = sum(1 for pod in ready if pod.node in stopped)
        left = len(ready) - lost
        services.append({"service": service.name, "ready": len(ready), "lost": lost, "left": left,
                         "state": "down" if left == 0 else "degraded" if lost else "ok"})
    return {"nodes": sorted(stopped), "services": services, "down": sorted(s["service"] for s in services if s["state"] == "down"),
            "degraded": sorted(s["service"] for s in services if s["state"] == "degraded")}


def observed_state(graph: DeploymentGraph, nodes: Sequence[str]) -> dict[str, Any]:
    """The same table from what was actually observed while the nodes were down: ready replicas per service, on nodes still up."""
    stopped = set(nodes)
    rows = []
    for service in graph.services:
        up = sum(1 for pod in service.pods if pod.ready and pod.node not in stopped)
        rows.append({"service": service.name, "readyOnSurvivingNodes": up, "declared": service.declared_replicas})
    return {"services": rows, "down": sorted(r["service"] for r in rows if r["declared"] and not r["readyOnSurvivingNodes"])}
