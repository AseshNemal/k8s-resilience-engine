from datetime import datetime, timezone
from typing import Optional

from .models import ApprovalModel, has_verified_provenance
from .repository import ApprovalRepository
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.approval import ApprovalStatus
from app.shared.schemas.recommendation import RecommendationReport

class StateTransitionError(Exception):
    pass

class VersionConflictError(Exception):
    pass

class PreconditionError(Exception):
    pass


class UnverifiedProvenanceError(EvidenceError):
    """The approval has no verifiable link to live cluster evidence, so it cannot authorise a change."""

    def __init__(self) -> None:
        super().__init__(
            "APPROVAL_PROVENANCE_UNVERIFIED",
            "This approval request has no verifiable link to live cluster evidence (it predates provenance "
            "tracking or came from non-live data), so it cannot be approved. Reject it and request a new "
            "approval from a current recommendation.",
            http_status=409,
            component="recommendation",
        )

class ApprovalService:
    def __init__(self, repository: ApprovalRepository):
        self.repository = repository

    def request_approval(
        self,
        recommendation_id: str,
        proposal_version: str,
        recommendation: RecommendationReport,
        yaml_snapshot: str | None = None,
        requested_by: str = "developer",
        evidence: dict | None = None,
    ) -> ApprovalModel:
        # Preconditions
        if not recommendation.recommendations:
            raise PreconditionError("Recommendation report has no candidates")
            
        candidate = next((c for c in recommendation.recommendations if c.id == recommendation_id), None)
        if not candidate:
            raise PreconditionError(f"Candidate {recommendation_id} not found in recommendation report")
            
        if not candidate.feasible:
            raise PreconditionError(f"Candidate {recommendation_id} is not feasible")

        existing = self.repository.get_by_recommendation_id(recommendation_id)
        if existing and existing.status == ApprovalStatus.PENDING_APPROVAL:
            raise StateTransitionError("Approval already pending")

        # Snapshot
        snapshot = candidate.model_dump()
        evaluation_snapshot = {
            "predicted_blast_radius": candidate.predicted_blast_radius,
            "risk_reduction": candidate.risk_reduction,
            "resource_cost": candidate.resource_cost,
            "confidence": candidate.confidence,
            "score": candidate.score,
        }
        if evidence is not None:
            evaluation_snapshot["provenance"] = dict(evidence)

        approval = ApprovalModel(
            id=0, # assigned by db
            recommendation_id=recommendation_id,
            proposal_version=proposal_version,
            status=ApprovalStatus.PENDING_APPROVAL,
            requested_at=datetime.now(timezone.utc),
            requested_by=requested_by,
            recommendation_snapshot=snapshot,
            yaml_snapshot=yaml_snapshot,
            evaluation_snapshot=evaluation_snapshot,
        )

        return self.repository.save(approval)

    def get_approval(self, recommendation_id: str) -> Optional[ApprovalModel]:
        return self.repository.get_by_recommendation_id(recommendation_id)

    def approve(
        self,
        recommendation_id: str,
        expected_proposal_version: str,
        note: str,
        decided_by: str = "developer",
    ) -> ApprovalModel:
        approval = self.repository.get_by_recommendation_id(recommendation_id)
        if not approval:
            raise PreconditionError("No approval requested for this recommendation")

        if approval.status != ApprovalStatus.PENDING_APPROVAL:
            raise StateTransitionError(f"Cannot approve from state {approval.status.value}")

        if approval.proposal_version != expected_proposal_version:
            raise VersionConflictError(
                f"Version mismatch: expected {expected_proposal_version}, got {approval.proposal_version}"
            )

        if not has_verified_provenance(approval):
            raise UnverifiedProvenanceError()

        approval.status = ApprovalStatus.APPROVED
        approval.decided_at = datetime.now(timezone.utc)
        approval.decided_by = decided_by
        approval.decision_note = note

        return self.repository.update(approval)

    def reject(
        self,
        recommendation_id: str,
        expected_proposal_version: str,
        note: str,
        decided_by: str = "developer",
    ) -> ApprovalModel:
        approval = self.repository.get_by_recommendation_id(recommendation_id)
        if not approval:
            raise PreconditionError("No approval requested for this recommendation")

        if approval.status != ApprovalStatus.PENDING_APPROVAL:
            raise StateTransitionError(f"Cannot reject from state {approval.status.value}")

        if approval.proposal_version != expected_proposal_version:
            raise VersionConflictError(
                f"Version mismatch: expected {expected_proposal_version}, got {approval.proposal_version}"
            )

        approval.status = ApprovalStatus.REJECTED
        approval.decided_at = datetime.now(timezone.utc)
        approval.decided_by = decided_by
        approval.decision_note = note

        return self.repository.update(approval)
