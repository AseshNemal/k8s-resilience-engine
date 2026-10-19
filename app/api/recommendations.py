from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Response

from app.engine.recommendation_evaluation_validation.approval import (
    ApprovalService,
    ApprovalRepository,
    LiveRecommendation,
    StateTransitionError,
    VersionConflictError,
    PreconditionError,
    has_verified_provenance,
)
from app.shared.evidence_errors import unavailable
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


def _no_live_recommendation() -> LiveRecommendation:
    raise unavailable(
        "RECOMMENDATION_EVIDENCE_UNAVAILABLE",
        "No recommendation has been generated from live cluster evidence, so there is nothing to approve.",
        "recommendation",
    )


def get_recommendation_provider() -> Callable[[], LiveRecommendation]:
    """Supplies the function that produces the recommendation (and live evidence) an approval is bound to.

    There is no live recommendation pipeline wired in yet, so the provider reports that explicitly; it
    must never fall back to sample data. When Component 4's live pipeline is integrated it replaces this
    dependency; tests substitute it with ``app.dependency_overrides``. The provider is called inside the
    handler, after the request body has been validated, so a malformed request is still a 422.
    """
    return _no_live_recommendation


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
) -> ApprovalResponse:
    try:
        approval = service.approve(
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