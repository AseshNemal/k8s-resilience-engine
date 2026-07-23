from dataclasses import dataclass

from app.shared.schemas.deployment import DeploymentGraph


@dataclass
class FeasibilityResult:
    feasible: bool
    reason: str


def check_topology_spread_feasibility(
    deployment: DeploymentGraph,
    service_name: str,
) -> FeasibilityResult:
    service = next(
        (
            item
            for item in deployment.services
            if item.name == service_name
        ),
        None,
    )

    if service is None:
        return FeasibilityResult(
            feasible=False,
            reason=f"Service not found: {service_name}",
        )

    ready_pods = [
        pod
        for pod in service.pods
        if pod.ready
    ]

    if len(ready_pods) < 2:
        return FeasibilityResult(
            feasible=False,
            reason="Topology spread requires at least two ready replicas.",
        )

    if len(deployment.nodes) < 2:
        return FeasibilityResult(
            feasible=False,
            reason="Topology spread requires at least two available nodes.",
        )

    return FeasibilityResult(
        feasible=True,
        reason="The service has enough replicas and available nodes.",
    )