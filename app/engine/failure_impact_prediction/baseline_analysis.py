"""Research baselines for Component 3 failure-impact experiments.

Each baseline deliberately observes only one layer of evidence.  Keeping the
limitations explicit prevents a baseline from accidentally becoming another
version of the proposed cross-layer method.
"""

from collections.abc import Collection
from dataclasses import dataclass

from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph


@dataclass(frozen=True)
class ReplicaCountBaseline:
    """Static service risk inferred without pod-placement information."""

    deployment_snapshot_id: str
    unavailable_services: tuple[str, ...]
    single_replica_services: tuple[str, ...]
    vulnerable_services: tuple[str, ...]
    vulnerable_service_percentage: float


@dataclass(frozen=True)
class DeploymentExposureBaseline:
    """Service impact inferred from placement without workflow dependencies."""

    deployment_snapshot_id: str
    failed_node: str
    node_induced_failures: tuple[str, ...]
    preexisting_unavailable_services: tuple[str, ...]
    unavailable_services: tuple[str, ...]
    unavailable_service_percentage: float


@dataclass(frozen=True)
class DependencyOnlyBaseline:
    """Workflow impact from an externally supplied service-failure scenario."""

    workflow_id: str
    unavailable_services: tuple[str, ...]
    failed_mandatory_services: tuple[str, ...]
    unavailable_optional_services: tuple[str, ...]
    unreferenced_services: tuple[str, ...]
    survives: bool


def _percentage(part: int, whole: int) -> float:
    if whole == 0:
        raise ValueError("At least one service is required for baseline analysis")
    return round((part / whole) * 100.0, 2)


def analyze_replica_count_only(
    deployment: DeploymentGraph,
) -> ReplicaCountBaseline:
    """Identify static risk using ready-replica counts only.

    A service with no ready replica is already unavailable.  A service with one
    ready replica is vulnerable to a single failure, but this baseline cannot
    identify which worker-node failure would remove that replica because it
    intentionally ignores pod placement.
    """

    unavailable = tuple(
        sorted(
            service.name
            for service in deployment.services
            if service.ready_replicas == 0
        )
    )
    single_replica = tuple(
        sorted(
            service.name
            for service in deployment.services
            if service.ready_replicas == 1
        )
    )
    vulnerable = tuple(sorted(set(unavailable) | set(single_replica)))

    return ReplicaCountBaseline(
        deployment_snapshot_id=deployment.snapshot_id,
        unavailable_services=unavailable,
        single_replica_services=single_replica,
        vulnerable_services=vulnerable,
        vulnerable_service_percentage=_percentage(
            len(vulnerable),
            len(deployment.services),
        ),
    )


def analyze_deployment_only(
    deployment: DeploymentGraph,
    failed_node: str,
) -> DeploymentExposureBaseline:
    """Predict service loss from ready-pod placement only.

    Workflow graphs and dependency criticality are intentionally not accepted.
    Therefore this baseline can report service impact, but cannot claim that a
    business workflow fails.
    """

    known_nodes = {node.name for node in deployment.nodes}
    if failed_node not in known_nodes:
        raise ValueError(f"Unknown failed node: {failed_node}")

    node_induced: list[str] = []
    preexisting: list[str] = []
    for service in deployment.services:
        ready_pods = tuple(pod for pod in service.pods if pod.ready)
        if not ready_pods:
            preexisting.append(service.name)
        elif all(pod.node == failed_node for pod in ready_pods):
            node_induced.append(service.name)

    node_induced_failures = tuple(sorted(node_induced))
    preexisting_unavailable = tuple(sorted(preexisting))
    unavailable = tuple(
        sorted(set(node_induced_failures) | set(preexisting_unavailable))
    )

    return DeploymentExposureBaseline(
        deployment_snapshot_id=deployment.snapshot_id,
        failed_node=failed_node,
        node_induced_failures=node_induced_failures,
        preexisting_unavailable_services=preexisting_unavailable,
        unavailable_services=unavailable,
        unavailable_service_percentage=_percentage(
            len(unavailable),
            len(deployment.services),
        ),
    )


def analyze_dependency_only(
    workflow: WorkflowGraph,
    unavailable_services: Collection[str],
) -> DependencyOnlyBaseline:
    """Evaluate dependencies without discovering infrastructure failures.

    The unavailable-service set must come from an external scenario.  This
    baseline only translates that set into workflow impact and never inspects a
    deployment graph or worker-node placement.
    """

    unavailable = set(unavailable_services)
    mandatory = set(workflow.mandatory_services)
    optional = set(workflow.optional_services)
    referenced = mandatory | optional

    failed_mandatory = tuple(
        service
        for service in workflow.mandatory_services
        if service in unavailable
    )
    unavailable_optional = tuple(
        service
        for service in workflow.optional_services
        if service in unavailable
    )

    return DependencyOnlyBaseline(
        workflow_id=workflow.workflow_id,
        unavailable_services=tuple(sorted(unavailable)),
        failed_mandatory_services=failed_mandatory,
        unavailable_optional_services=unavailable_optional,
        unreferenced_services=tuple(sorted(unavailable - referenced)),
        survives=not failed_mandatory,
    )


__all__ = [
    "DependencyOnlyBaseline",
    "DeploymentExposureBaseline",
    "ReplicaCountBaseline",
    "analyze_dependency_only",
    "analyze_deployment_only",
    "analyze_replica_count_only",
]
