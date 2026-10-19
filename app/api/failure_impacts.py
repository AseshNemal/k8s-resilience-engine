from fastapi import APIRouter, HTTPException, Query, Response

from app.engine.failure_impact_prediction.service import (
    predict_runtime_failure_impact_with_evidence,
)
from app.shared.schemas.failure_impact import FailureImpactReport


router = APIRouter(
    prefix="/api/failure-impacts",
    tags=["Failure Impact Prediction"],
)


@router.get(
    "/runtime",
    response_model=FailureImpactReport,
    response_model_by_alias=True,
    responses={
        404: {"description": "No frozen workflow graph exists for the workflow"},
        409: {"description": "The discovered topology is not complete enough for this workflow"},
        422: {"description": "The runtime inputs or failed node are invalid"},
        502: {"description": "The connected cluster could not be read"},
        503: {"description": "No cluster is connected, or an upstream component input is unavailable"},
    },
)
def simulate_runtime_failure(
    response: Response,
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
    """Predict the impact of a virtual worker-node failure on the connected cluster's real topology.

    The result is a prediction, not an observation. Evidence problems (no cluster, cluster errors,
    incomplete topology, no frozen workflow) are explicit errors with stable codes. The topology's
    status, capture time and warning codes are returned in ``X-Topology-*`` headers.
    """

    try:
        report, evidence = predict_runtime_failure_impact_with_evidence(
            workflow_id, failed_node, namespace
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    codes = sorted({w.code for w in evidence.topology_warnings} | {u.reason for u in evidence.topology_unresolved})
    response.headers["X-Topology-Status"] = evidence.topology_status
    response.headers["X-Topology-Captured-At"] = evidence.topology_captured_at
    response.headers["X-Topology-Warnings"] = ",".join(codes)
    return report
