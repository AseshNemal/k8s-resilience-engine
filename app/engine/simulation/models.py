from dataclasses import dataclass

from app.shared.schemas.deployment import DeploymentGraph


@dataclass(frozen=True)
class DeploymentSimulationResult:
    """Before/after deployment snapshots for one virtual simulation."""

    before: DeploymentGraph
    after: DeploymentGraph
