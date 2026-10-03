from fastapi import APIRouter, Depends, HTTPException

from app.engine.recommendation_evaluation_validation.service import (
    get_sample_recommendation,
)
from app.engine.recommendation_evaluation_validation.approval import (
    ApprovalService,
    ApprovalRepository,
    StateTransitionError,
    VersionConflictError,
    PreconditionError,
)
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


@router.get(
    "/sample",
    response_model=RecommendationReport,
    response_model_by_alias=True,
)
def read_sample_recommendation() -> RecommendationReport:
    return get_sample_recommendation()


@router.get(
    "/{recommendation_id}/approval",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def get_approval(
    recommendation_id: str,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalResponse:
    approval = service.get_approval(recommendation_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    # Convert dataclass ApprovalModel to pydantic ApprovalResponse
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


@router.post(
    "/{recommendation_id}/request-approval",
    response_model=ApprovalResponse,
    response_model_by_alias=True,
)
def request_approval(
    recommendation_id: str,
    request: ApprovalRequest,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalResponse:
    # We must load the real recommendation report to extract the snapshot.
    # In a real system, this would fetch from a database. For now, use the sample.
    recommendation = get_sample_recommendation()
    
    try:
        approval = service.request_approval(
            recommendation_id=recommendation_id,
            proposal_version=request.proposal_version,
            recommendation=recommendation,
            yaml_snapshot=request.yaml_snapshot,
            requested_by="developer",
        )
    except PreconditionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return get_approval(recommendation_id, service)


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

    return get_approval(recommendation_id, service)


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

    return get_approval(recommendation_id, service)