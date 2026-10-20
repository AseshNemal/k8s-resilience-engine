from fastapi import APIRouter, HTTPException, Query

from app.engine.failure_impact_prediction.service import (
    RuntimeInputUnavailableError,
    build_portfolio_failure_impact_report,
    get_demo_deployment,
    get_sample_failure_impact,
    predict_demo_failure_impact,
    predict_demo_portfolio_failure_impact,
    predict_runtime_failure_impact,
    predict_runtime_portfolio_failure_impact,
)
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import (
    FailureImpactReport,
    PortfolioFailureImpactReport,
)


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


@router.get(
    "/portfolio/simulate",
    response_model=PortfolioFailureImpactReport,
    response_model_by_alias=True,
    responses={422: {"description": "The selected worker node is unknown"}},
)
def simulate_demo_portfolio_failure(
    failed_node: str = Query(
        "node-1",
        min_length=1,
        description="Worker node to remove from the demo portfolio topology",
    ),
) -> PortfolioFailureImpactReport:
    """Run a node-failure simulation across disclosed workflow fixtures."""

    try:
        prediction = predict_demo_portfolio_failure_impact(failed_node)
        return build_portfolio_failure_impact_report(prediction)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/runtime",
    response_model=FailureImpactReport,
    response_model_by_alias=True,
    responses={
        404: {"description": "A frozen workflow graph was not found"},
        422: {"description": "The runtime inputs or failed node are invalid"},
        503: {"description": "An upstream component input is unavailable"},
    },
)
def simulate_runtime_failure(
    workflow_id: str = Query(
        ...,
        min_length=1,
        description="Component 1 workflow identifier with a frozen graph",
    ),
    failed_node: str = Query(
        ...,
        min_length=1,
        description="Worker node to remove virtually from the live topology",
    ),
    namespace: str = Query(
        "default",
        min_length=1,
        description="Kubernetes namespace collected by Component 2",
    ),
) -> FailureImpactReport:
    """Run Component 3 over validated workflow and current topology inputs."""

    try:
        return predict_runtime_failure_impact(
            workflow_id,
            failed_node,
            namespace,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeInputUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/portfolio/runtime",
    response_model=PortfolioFailureImpactReport,
    response_model_by_alias=True,
    responses={
        404: {"description": "A frozen workflow graph was not found"},
        422: {"description": "The runtime inputs or failed node are invalid"},
        503: {"description": "An upstream component input is unavailable"},
    },
)
def simulate_runtime_portfolio_failure(
    workflow_ids: list[str] = Query(
        ...,
        alias="workflow_id",
        min_length=1,
        description="Repeat this parameter for each frozen workflow ID",
    ),
    failed_node: str = Query(
        ...,
        min_length=1,
        description="Worker node to remove virtually from the live topology",
    ),
    namespace: str = Query(
        "default",
        min_length=1,
        description="Kubernetes namespace collected by Component 2",
    ),
) -> PortfolioFailureImpactReport:
    """Run portfolio prediction over frozen workflows and live topology."""

    try:
        prediction = predict_runtime_portfolio_failure_impact(
            tuple(workflow_ids),
            failed_node,
            namespace,
        )
        return build_portfolio_failure_impact_report(prediction)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeInputUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
