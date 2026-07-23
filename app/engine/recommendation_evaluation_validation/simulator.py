from app.shared.schemas.deployment import DeploymentGraph


def simulate_topology_spread(
    deployment: DeploymentGraph,
    service_name: str,
) -> DeploymentGraph:
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

    if len(deployment.nodes) < 2:
        raise ValueError(
            "Topology spread requires more than one available node."
        )

    for index, pod in enumerate(service.pods):
        target_node = deployment.nodes[index % len(deployment.nodes)]

        pod.node = target_node.name
        pod.zone = target_node.zone

    return deployment