from copy import deepcopy

from app.shared.schemas.deployment import DeploymentGraph


def create_virtual_deployment(
    deployment: DeploymentGraph,
) -> DeploymentGraph:
    return deepcopy(deployment)