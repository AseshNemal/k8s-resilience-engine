from typing import Protocol

from app.shared.schemas.deployment import DeploymentGraph


class DeploymentMutation(Protocol):
    """Contract for a virtual deployment change.

    Implementations belong to the component that owns the research logic.
    The shared simulation engine only provides an isolated DeploymentGraph.
    """

    def apply(
        self,
        deployment: DeploymentGraph,
    ) -> DeploymentGraph:
        """Apply a virtual change and return the modified deployment."""
        ...
