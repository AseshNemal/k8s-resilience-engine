from app.shared.schemas.deployment import DeploymentGraph


def simulate_topology_spread(
    deployment: DeploymentGraph,
    service_name: str,
) -> DeploymentGraph:
    """Distribute a service's ready pods across available nodes."""

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

    for index, pod in enumerate(ready_pods):
        target_node = deployment.nodes[
            index % len(deployment.nodes)
        ]

        pod.node = target_node.name
        pod.zone = target_node.zone

    return deployment