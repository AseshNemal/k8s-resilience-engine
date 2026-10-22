"""
Component 4 deployment mutations.

Each class here is a concrete DeploymentMutation that encodes one
recommendation strategy.  The shared simulate_deployment_change() engine
calls apply() on a *deep-copied* working graph, so the caller's original
DeploymentGraph is never touched.

Topology-spread placement logic lives here, not in app/engine/simulation/.
"""

from collections.abc import Mapping

from app.engine.recommendation_evaluation_validation.simulator import (
    simulate_topology_spread,
)
from app.shared.schemas.deployment import DeploymentGraph


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
