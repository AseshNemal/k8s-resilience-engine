from collections import Counter
from collections.abc import Mapping

from app.shared.schemas.deployment import DeploymentGraph, ServicePlacement

# The node name the graph builder gives a pod the scheduler has not placed.
_UNSCHEDULED = "unscheduled"


def refresh_placement_summary(service: ServicePlacement) -> None:
    """Recompute the fields a ServicePlacement derives from its pods, after a virtual change moved or added pods.

    Component 2's definitions (``placement_metrics``), over ready replicas that are bound to a node: concentrated means
    they all share one node, the concentration is the largest share on one node, and an exposed failure domain is a node
    whose loss leaves none (so there is one only when they all share it). Without this a simulated graph would show its
    pods on several nodes while still describing the service as concentrated on the old one.
    """
    per_node = Counter(pod.node for pod in service.pods if pod.ready and pod.node != _UNSCHEDULED)
    total = sum(per_node.values())
    service.ready_replicas = sum(1 for pod in service.pods if pod.ready)
    service.hosting_nodes = sorted({pod.node for pod in service.pods if pod.node != _UNSCHEDULED})
    service.concentrated = len(per_node) == 1 if per_node else None
    service.replica_concentration = max(per_node.values()) / total if total else None
    service.exposed_failure_domains = sorted(per_node) if len(per_node) == 1 else []


def simulate_topology_spread(
    deployment: DeploymentGraph,
    service_name: str,
    placement: Mapping[str, str] | None = None,
) -> DeploymentGraph:
    """Distribute a service's ready pods across available nodes.

    ``placement`` (pod name -> node name) is the placement the feasibility analysis verified. When it is
    given the pods go exactly there; without it they are spread round-robin over every node, which ignores
    whether a node can actually take them.

    The result is a TARGET state, not what applying a spread constraint does: that constraint only filters
    where newly scheduled pods may go and never moves a running pod. The pods whose node differs from today's
    are ``FeasibilityResult.pods_to_recreate`` (placement churn). The change that applies the constraint edits
    the Deployment's pod template and so rolls out every pod of the Deployment; that scope is not simulated.
    """

    service = next(
        (
            item
            for item in deployment.services
            if item.name == service_name
        ),
        None,
    )

    if service is None:
        raise ValueError(f"Service not found: {service_name}")

    if not deployment.nodes:
        raise ValueError("No available nodes found.")

    ready_pods = [
        pod
        for pod in service.pods
        if pod.ready
    ]

    if not ready_pods:
        raise ValueError(
            f"No ready pods found for service: {service_name}"
        )

    nodes = {node.name: node for node in deployment.nodes}
    for index, pod in enumerate(ready_pods):
        if placement is not None:
            if pod.name not in placement or placement[pod.name] not in nodes:
                raise ValueError(f"No verified placement for pod: {pod.name}")
            target_node = nodes[placement[pod.name]]
        else:
            target_node = deployment.nodes[
                index % len(deployment.nodes)
            ]

        pod.node = target_node.name
        pod.zone = target_node.zone

    refresh_placement_summary(service)
    return deployment
