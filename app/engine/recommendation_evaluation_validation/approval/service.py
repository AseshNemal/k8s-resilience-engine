from collections.abc import Callable
from datetime import datetime, timezone
from typing import Optional

from .eligibility import evaluate_eligibility
from .models import ApprovalModel, LiveRecommendation, has_verified_provenance
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

# Approving a candidate that is not the top-ranked one needs a written reason of at least this many characters.
MIN_JUSTIFICATION_CHARS = 20


class ApprovalService:
    """The approval gate.

    A candidate can be put forward, and later approved, only when the server's own evaluation says it is
    verified feasible and ranked with a positive score (``eligibility.py``). The evaluation is the one passed in
    when the request is made and a fresh one when it is decided, so a request written before this policy, or
    one the cluster has since overtaken, is judged by what is true now. The top-ranked candidate is the
    automatic selection; any other ranked candidate needs a documented justification in the approval note.
    """

    def __init__(self, repository: ApprovalRepository, min_justification_chars: int = MIN_JUSTIFICATION_CHARS):
        self.repository = repository
        self.min_justification_chars = min_justification_chars

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

        standing = evaluate_eligibility(LiveRecommendation(report=recommendation, evidence=evidence or {}),
                                        recommendation_id)
        if not standing.eligible:
            raise PreconditionError(standing.reason)

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
        evaluation_snapshot["selection"] = standing.to_dict()
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
        evaluate: Callable[[ApprovalModel], LiveRecommendation] | None = None,
    ) -> ApprovalModel:
        """Approve a pending request.

        ``evaluate`` re-runs the server's evaluation for the workflow and failure the request was made for. It
        is called only after the state, version and provenance checks pass, and without it nothing can be
        approved: eligibility is never taken from what was stored when the request was made.
        """
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

        if evaluate is None:
            raise PreconditionError("There is no current evaluation to check this candidate against, so it "
                                    "cannot be approved")
        standing = evaluate_eligibility(evaluate(approval), recommendation_id)
        if not standing.eligible:
            raise PreconditionError(standing.reason)
        justification = (note or "").strip()
        if standing.requires_justification and len(justification) < self.min_justification_chars:
            raise PreconditionError(
                f"Candidate {recommendation_id} is ranked {standing.rank}; the top-ranked candidate is "
                f"{standing.selected_recommendation_id}. Approving another candidate needs a documented "
                f"justification of at least {self.min_justification_chars} characters in the note.")

        decided_at = datetime.now(timezone.utc)
        approval.status = ApprovalStatus.APPROVED
        approval.decided_at = decided_at
        approval.decided_by = decided_by
        approval.decision_note = note
        approval.evaluation_snapshot = {**(approval.evaluation_snapshot or {}), "approvalCheck": {
            **standing.to_dict(), "checkedAt": decided_at.isoformat(),
            "justification": justification if standing.requires_justification else None}}

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
