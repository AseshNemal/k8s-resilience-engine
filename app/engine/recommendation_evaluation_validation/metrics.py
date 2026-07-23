from collections import Counter

from app.shared.schemas.deployment import DeploymentGraph


def calculate_replica_concentration(
    deployment: DeploymentGraph,
    service_name: str,
) -> float:
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

    ready_pods = [
        pod
        for pod in service.pods
        if pod.ready
    ]

    if not ready_pods:
        return 0.0

    node_counts = Counter(
        pod.node
        for pod in ready_pods
    )

    largest_group = max(node_counts.values())

    return largest_group / len(ready_pods)