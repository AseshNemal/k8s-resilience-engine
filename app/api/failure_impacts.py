from fastapi import APIRouter, HTTPException, Query

from app.engine.failure_impact_prediction.service import (
    get_demo_deployment,
    get_sample_failure_impact,
    predict_demo_failure_impact,
)
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import FailureImpactReport


router = APIRouter(
    prefix="/api/failure-impacts",
    tags=["Failure Impact Prediction"],
)


@router.get(
    "/sample",
    response_model=FailureImpactReport,
    response_model_by_alias=True,
)
def read_sample_failure_impact() -> FailureImpactReport:
    return get_sample_failure_impact()


@router.get(
    "/demo-deployment",
    response_model=DeploymentGraph,
    response_model_by_alias=True,
)
def read_demo_deployment() -> DeploymentGraph:
    """Return disclosed synthetic placements completing current upstream samples."""

    return get_demo_deployment()


@router.get(
    "/simulate",
    response_model=FailureImpactReport,
    response_model_by_alias=True,
    responses={422: {"description": "The selected worker node is unknown"}},
)
def simulate_demo_failure(
    failed_node: str = Query(
        "node-1",
        min_length=1,
        description="Worker node to remove virtually from the demo topology",
    ),
) -> FailureImpactReport:
    """Run Component 3's deterministic engine over contract-compatible fixtures."""

    try:
        return predict_demo_failure_impact(failed_node)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
