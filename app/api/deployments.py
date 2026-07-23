from fastapi import APIRouter

from app.engine.deployment_topology_modeling.service import (
    get_sample_deployment,
)
from app.shared.schemas.deployment import DeploymentGraph


router = APIRouter(
    prefix="/api/deployments",
    tags=["Deployment Topology Modeling"],
)


@router.get(
    "/sample",
    response_model=DeploymentGraph,
    response_model_by_alias=True,
)
def read_sample_deployment() -> DeploymentGraph:
    return get_sample_deployment()