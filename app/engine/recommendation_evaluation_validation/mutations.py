"""
Component 4 deployment mutations.

Each class here is a concrete DeploymentMutation that encodes one
recommendation strategy.  The shared simulate_deployment_change() engine
calls apply() on a *deep-copied* working graph, so the caller's original
DeploymentGraph is never touched.

Topology-spread placement logic lives here, not in app/engine/simulation/.
"""

from collections.abc import Mapping, Sequence

from app.engine.recommendation_evaluation_validation.simulator import (
    simulate_topology_spread,
)
from app.shared.schemas.deployment import DeploymentGraph, PodModel


class TopologySpreadMutation:
    """Redistribute ready replicas of a service across available nodes.

    Implements the DeploymentMutation protocol so it can be passed directly
    to simulate_deployment_change().
    """

    def __init__(self, service_name: str, placement: Mapping[str, str] | None = None) -> None:
        self._service_name = service_name
        self._placement = placement

    def apply(self, deployment: DeploymentGraph) -> DeploymentGraph:
        """Apply topology-spread redistribution to the working copy."""
        return simulate_topology_spread(deployment, self._service_name, self._placement)


class ReplicaIncreaseMutation:
    """Add replicas to a single-replica service and place everything at the verified nodes.

    ``added`` names the virtual replicas (they are not existing pods); ``placement`` maps every pod, existing and
    added, to its verified node. Existing pods move only if the verified placement says so.
    """

    def __init__(self, service_name: str, placement: Mapping[str, str], added: Sequence[str]) -> None:
        self._service_name, self._placement, self._added = service_name, placement, tuple(added)

    def apply(self, deployment: DeploymentGraph) -> DeploymentGraph:
        service = next((s for s in deployment.services if s.name == self._service_name), None)
        if service is None:
            raise ValueError(f"Service not found: {self._service_name}")
        nodes = {node.name: node for node in deployment.nodes}
        template = next((pod for pod in service.pods if pod.ready), None)
        if template is None:
            raise ValueError(f"No ready pods found for service: {self._service_name}")
        for name in self._added:
            service.pods.append(template.model_copy(update={"name": name, "restart_count": None}))
        for pod in service.pods:
            if pod.name not in self._placement or self._placement[pod.name] not in nodes:
                raise ValueError(f"No verified placement for pod: {pod.name}")
            pod.node = self._placement[pod.name]
            pod.zone = nodes[pod.node].zone
        service.declared_replicas += len(self._added)
        service.ready_replicas = sum(1 for pod in service.pods if pod.ready)
        if service.declared_intent is not None:
            service.declared_intent.declared_replicas += len(self._added)
        service.hosting_nodes = sorted({pod.node for pod in service.pods})
        service.exposed_failure_domains = list(service.hosting_nodes)
        ready_nodes = [pod.node for pod in service.pods if pod.ready]
        service.concentrated = len(set(ready_nodes)) == 1 if ready_nodes else None
        service.replica_concentration = (max(ready_nodes.count(n) for n in set(ready_nodes)) / len(ready_nodes)
                                         if ready_nodes else None)
        return deployment
