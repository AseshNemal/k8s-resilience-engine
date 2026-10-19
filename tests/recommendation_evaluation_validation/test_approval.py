import pytest
from app.engine.recommendation_evaluation_validation.approval import (
    ApprovalService,
    ApprovalRepository,
    StateTransitionError,
    VersionConflictError,
    PreconditionError,
    UnverifiedProvenanceError,
)
from app.shared.schemas.approval import ApprovalStatus
from app.shared.schemas.recommendation import RecommendationReport, RecommendationCandidate

from unittest.mock import MagicMock

@pytest.fixture
def mock_repository():
    return MagicMock(spec=ApprovalRepository)

@pytest.fixture
def approval_service(mock_repository):
    return ApprovalService(mock_repository)

@pytest.fixture
def sample_recommendation():
    return RecommendationReport(
        id="report-1",
        workflow_id="wf-1",
        recommendations=[
            RecommendationCandidate(
                id="rec-1",
                type="topology-spread",
                service="auth-service",
                feasible=True,
                predicted_blast_radius=0.1,
                risk_reduction=15.0,
                resource_cost="Medium",
                confidence=0.85,
                score=0.9
            ),
            RecommendationCandidate(
                id="rec-2",
                type="pod-anti-affinity",
                service="db-service",
                feasible=False,
                predicted_blast_radius=0.2,
                risk_reduction=5.0,
                resource_cost="High",
                confidence=0.5,
                score=0.3
            )
        ],
        selected_recommendation_id="rec-1"
    )

def test_request_approval_success(approval_service, mock_repository, sample_recommendation):
    mock_repository.get_by_recommendation_id.return_value = None
    mock_repository.save.return_value = "saved_approval"

    approval = approval_service.request_approval(
        recommendation_id="rec-1",
        proposal_version="v1.0",
        recommendation=sample_recommendation
    )

    assert approval == "saved_approval"
    mock_repository.save.assert_called_once()
    saved_arg = mock_repository.save.call_args[0][0]
    assert saved_arg.recommendation_id == "rec-1"
    assert saved_arg.status == ApprovalStatus.PENDING_APPROVAL
    assert saved_arg.proposal_version == "v1.0"

def test_request_approval_fails_if_already_pending(approval_service, mock_repository, sample_recommendation):
    existing = MagicMock()
    existing.status = ApprovalStatus.PENDING_APPROVAL
    mock_repository.get_by_recommendation_id.return_value = existing

    with pytest.raises(StateTransitionError):
        approval_service.request_approval(
            recommendation_id="rec-1",
            proposal_version="v1.0",
            recommendation=sample_recommendation
        )

def test_request_approval_fails_if_no_candidates(approval_service):
    report = RecommendationReport(id="r", workflow_id="w", recommendations=[])
    with pytest.raises(PreconditionError):
        approval_service.request_approval(
            recommendation_id="rec-1",
            proposal_version="v1.0",
            recommendation=report
        )

def test_request_approval_fails_if_candidate_not_found(approval_service, sample_recommendation):
    with pytest.raises(PreconditionError):
        approval_service.request_approval(
            recommendation_id="rec-unknown",
            proposal_version="v1.0",
            recommendation=sample_recommendation
        )

def test_request_approval_fails_if_candidate_not_feasible(approval_service, sample_recommendation):
    with pytest.raises(PreconditionError):
        approval_service.request_approval(
            recommendation_id="rec-2",
            proposal_version="v1.0",
            recommendation=sample_recommendation
        )

def test_get_approval_success(approval_service, mock_repository):
    mock_repository.get_by_recommendation_id.return_value = "approval"
    assert approval_service.get_approval("rec-1") == "approval"

def test_get_approval_returns_none_if_not_found(approval_service, mock_repository):
    mock_repository.get_by_recommendation_id.return_value = None
    assert approval_service.get_approval("rec-1") is None

def test_approve_refused_without_live_provenance(approval_service, mock_repository):
    pending = MagicMock()
    pending.status = ApprovalStatus.PENDING_APPROVAL
    pending.proposal_version = "v1.0"
    pending.evaluation_snapshot = {"score": 0.9}  # no provenance marker
    mock_repository.get_by_recommendation_id.return_value = pending

    with pytest.raises(UnverifiedProvenanceError):
        approval_service.approve("rec-1", "v1.0", "Looks good")

    mock_repository.update.assert_not_called()
    assert pending.status == ApprovalStatus.PENDING_APPROVAL


def test_approve_success(approval_service, mock_repository):
    pending = MagicMock()
    pending.status = ApprovalStatus.PENDING_APPROVAL
    pending.proposal_version = "v1.0"
    pending.evaluation_snapshot = {"provenance": {"source": "live-discovery"}}
    mock_repository.get_by_recommendation_id.return_value = pending
    mock_repository.update.return_value = "updated"

    result = approval_service.approve("rec-1", "v1.0", "Looks good")
    
    assert pending.status == ApprovalStatus.APPROVED
    assert pending.decision_note == "Looks good"
    assert result == "updated"

def test_approve_fails_if_not_pending(approval_service, mock_repository):
    approved = MagicMock()
    approved.status = ApprovalStatus.APPROVED
    mock_repository.get_by_recommendation_id.return_value = approved

    with pytest.raises(StateTransitionError):
        approval_service.approve("rec-1", "v1.0", "Looks good")

def test_approve_fails_if_version_mismatch(approval_service, mock_repository):
    pending = MagicMock()
    pending.status = ApprovalStatus.PENDING_APPROVAL
    pending.proposal_version = "v2.0"
    mock_repository.get_by_recommendation_id.return_value = pending

    with pytest.raises(VersionConflictError):
        approval_service.approve("rec-1", "v1.0", "Looks good")

def test_reject_success(approval_service, mock_repository):
    pending = MagicMock()
    pending.status = ApprovalStatus.PENDING_APPROVAL
    pending.proposal_version = "v1.0"
    mock_repository.get_by_recommendation_id.return_value = pending
    mock_repository.update.return_value = "updated"

    result = approval_service.reject("rec-1", "v1.0", "Not needed")
    
    assert pending.status == ApprovalStatus.REJECTED
    assert pending.decision_note == "Not needed"
    assert result == "updated"

def test_reject_fails_if_not_pending(approval_service, mock_repository):
    rejected = MagicMock()
    rejected.status = ApprovalStatus.REJECTED
    mock_repository.get_by_recommendation_id.return_value = rejected

    with pytest.raises(StateTransitionError):
        approval_service.reject("rec-1", "v1.0", "Not needed")

def test_reject_fails_if_version_mismatch(approval_service, mock_repository):
    pending = MagicMock()
    pending.status = ApprovalStatus.PENDING_APPROVAL
    pending.proposal_version = "v2.0"
    mock_repository.get_by_recommendation_id.return_value = pending

    with pytest.raises(VersionConflictError):
        approval_service.reject("rec-1", "v1.0", "Not needed")
