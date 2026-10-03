from copy import deepcopy

from app.engine.simulation.models import DeploymentSimulationResult
from app.engine.simulation.protocols import DeploymentMutation
from app.shared.schemas.deployment import DeploymentGraph


def simulate_deployment_change(
    deployment: DeploymentGraph,
    mutation: DeploymentMutation,
) -> DeploymentSimulationResult:
    """Run a component-owned virtual change without mutating the input graph."""

    before = deepcopy(deployment)
    working_copy = deepcopy(deployment)

    after = mutation.apply(working_copy)

    if not isinstance(after, DeploymentGraph):
        raise TypeError(
            "Deployment mutations must return a DeploymentGraph."
        )

    return DeploymentSimulationResult(
        before=before,
        after=after,
    )
