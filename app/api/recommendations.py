from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.engine.recommendation_evaluation_validation.approval import (
    ApprovalModel,
    ApprovalService,
    ApprovalRepository,
    LiveRecommendation,
    StateTransitionError,
    VersionConflictError,
    PreconditionError,
    has_verified_provenance,
)
from app.engine.recommendation_evaluation_validation.evidence_context import build_recommendation_context
from app.engine.recommendation_evaluation_validation.explanation import describe_live_recommendation
from app.engine.recommendation_evaluation_validation.live_pipeline import build_live_recommendation
from app.shared.evidence_errors import EvidenceError, unavailable
from app.core.config import get_settings
from app.core.database import get_engine
from sqlalchemy.orm import Session

from app.shared.schemas.recommendation import RecommendationReport
from app.shared.schemas.approval import (
    ApprovalRequest,
    ApprovalDecisionRequest,
    ApprovalResponse,
)

router = APIRouter(
    prefix="/api/recommendations",
    tags=["Recommendation Evaluation"],
)


def get_db():
    engine = get_engine(get_settings().database_url)
    with Session(engine) as session:
        yield session

def get_approval_service(db: Session = Depends(get_db)) -> ApprovalService:
    repository = ApprovalRepository(db)
    return ApprovalService(repository)


def get_recommendation_provider(
    workflow_id: str | None = Query(None, description="Frozen workflow to recommend for (required)"),
    failed_node: str | None = Query(None, description="Worker to protect against; default: the one whose loss "
                                                       "would hurt most"),
    namespace: str = Query("default", description="Kubernetes namespace collected by Component 2"),
) -> Callable[[], LiveRecommendation]:
    """Supplies the function that produces the recommendation (and live evidence) an approval is bound to.

    The recommendation is computed from the connected cluster and the named frozen workflow
    (live_pipeline.py); it never falls back to sample data. Without a workflow there is nothing to compute
    for, which is said explicitly. The provider is called inside the handler, after the request body has
    been validated, so a malformed request is still a 422. Tests substitute it with
    ``app.dependency_overrides``.
    """

    def provide() -> LiveRecommendation:
        if not workflow_id:
            raise unavailable(
                "RECOMMENDATION_EVIDENCE_UNAVAILABLE",
                "Name the frozen workflow to recommend for (workflow_id). No recommendation has been generated "
                "from live cluster evidence, so there is nothing to approve.",
                "recommendation",
            )
        try:
            return build_live_recommendation(workflow_id, failed_node, namespace)
        except ValueError as error:  # a blank identifier
            raise EvidenceError(
                "INVALID_RECOMMENDATION_REQUEST", str(error), http_status=422, component="recommendation"
            ) from error

    return provide


def get_current_evaluator() -> Callable[[ApprovalModel], LiveRecommendation]:
    """Supplies the function that re-evaluates, on the server, the workflow and failure a request was made for.

    An approval is decided against the evaluation as it is now, not as it was when the request was written. The
    workflow, namespace and failed node come from the provenance the server stored with the request, never from
    the client. Tests substitute it with ``app.dependency_overrides``.
    """

    def evaluate(approval: ApprovalModel) -> LiveRecommendation:
        provenance = (approval.evaluation_snapshot or {}).get("provenance") or {}
        workflow_id, namespace = provenance.get("workflowId"), provenance.get("namespace")
        if not workflow_id or not namespace:
            raise PreconditionError(
                "This approval request does not record the workflow and namespace it was computed for, so it "
                "cannot be re-evaluated; reject it and request a new approval from a current recommendation.")
        try:
            return build_live_recommendation(workflow_id, provenance.get("failedNode"), namespace)
        except ValueError as error:
            raise EvidenceError(
                "INVALID_RECOMMENDATION_REQUEST", str(error), http_status=422, component="recommendation"
            ) from error

    return evaluate


def _to_response(approval) -> ApprovalResponse:
    return ApprovalResponse(
        id=approval.id,
        recommendation_id=approval.recommendation_id,
        proposal_version=approval.proposal_version,
        status=approval.status,
        requested_at=approval.requested_at,
        requested_by=approval.requested_by,
        decided_at=approval.decided_at,
        decided_by=approval.decided_by,
        decision_note=approval.decision_note,
        recommendation_snapshot=approval.recommendation_snapshot,
        yaml_snapshot=approval.yaml_snapshot,
        evaluation_snapshot=approval.evaluation_snapshot,
    )


@router.get(
    "/live",
    response_model=RecommendationReport,
    response_model_by_alias=True,
    responses={409: {"description": "No recommendation has been generated from live evidence"}},
)
def read_live_recommendation(
    response: Response,
    provider: Callable[[], LiveRecommendation] = Depends(get_recommendation_provider),
) -> RecommendationReport:
    """The current recommendation report and the live evidence it was computed from, or an explicit error."""
    live = provider()
    response.headers["X-Evidence-Source"] = str(live.evidence.get("source", ""))
    response.headers["X-Topology-Captured-At"] = str(live.evidence.get("topologyCapturedAt", ""))
    return live.report


@router.get("/context")
def read_recommendation_context(
    namespace: str | None = Query(None, description="Application namespace; default: the only one with applications"),
    workflow_id: str | None = Query(None, description="Frozen workflow; default: the only one that applies"),
) -> dict:
    """Which application and frozen workflow the page can use, and which evidence is missing, and what to do.

    Read-only and always answers: a missing piece of evidence is the answer, not an error. It lists the approved
    frozen workflows and whether each applies to the application's services; it builds none of them.
    """
    return build_recommendation_context(namespace, workflow_id)


@router.get(
    "/live/detail",
    responses={409: {"description": "No recommendation has been generated from live evidence"}},
)
def read_live_recommendation_detail(
    response: Response,
    provider: Callable[[], LiveRecommendation] = Depends(get_recommendation_provider),
) -> dict:
    """The live recommendation with what the page shows for each candidate: root cause, the change, feasibility and
    why, current placement, predicted before and after, cost, ranking and whether it may be put to approval.

    Same inputs and same coded errors as ``/live``. Everything in it was computed by the engine; nothing is applied.
    """
    live = provider()
    response.headers["X-Evidence-Source"] = str(live.evidence.get("source", ""))
    response.headers["X-Topology-Captured-At"] = str(live.evidence.get("topologyCapturedAt", ""))
    return describe_live_recommendation(live)


@router.get(
    "/{recommendation_id}/approval",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def get_approval(
    recommendation_id: str,
    response: Response,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalResponse:
    approval = service.get_approval(recommendation_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    response.headers["X-Evidence-Provenance"] = "verified" if has_verified_provenance(approval) else "unverified"
    return _to_response(approval)


@router.post(
    "/{recommendation_id}/request-approval",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def request_approval(
    recommendation_id: str,
    request: ApprovalRequest,
    response: Response,
    service: ApprovalService = Depends(get_approval_service),
    provider: Callable[[], LiveRecommendation] = Depends(get_recommendation_provider),
) -> ApprovalResponse:
    live = provider()
    try:
        approval = service.request_approval(
            recommendation_id=recommendation_id,
            proposal_version=request.proposal_version,
            recommendation=live.report,
            yaml_snapshot=request.yaml_snapshot,
            requested_by="developer",
            evidence=live.evidence,
        )
    except PreconditionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    response.headers["X-Evidence-Provenance"] = "verified" if has_verified_provenance(approval) else "unverified"
    return _to_response(approval)


@router.post(
    "/{recommendation_id}/approve",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def approve_recommendation(
    recommendation_id: str,
    request: ApprovalDecisionRequest,
    service: ApprovalService = Depends(get_approval_service),
    evaluator: Callable[[ApprovalModel], LiveRecommendation] = Depends(get_current_evaluator),
) -> ApprovalResponse:
    try:
        approval = service.approve(
            recommendation_id=recommendation_id,
            expected_proposal_version=request.expected_proposal_version,
            note=request.note,
            decided_by="developer",
            evaluate=evaluator,
        )
    except PreconditionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except VersionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return _to_response(approval)


@router.post(
    "/{recommendation_id}/reject",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def reject_recommendation(
    recommendation_id: str,
    request: ApprovalDecisionRequest,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalResponse:
    try:
        approval = service.reject(
            recommendation_id=recommendation_id,
            expected_proposal_version=request.expected_proposal_version,
            note=request.note,
            decided_by="developer",
        )
    except PreconditionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except VersionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return _to_response(approval)