import hashlib
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

def _sha256(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


SERVER_GENERATED = "server-generated"
CLIENT_SUPPLIED = "client-supplied"


def _yaml_record(yaml_snapshot: str | None, server_generated: bool = False) -> dict:
    """What is recorded about the YAML stored with a request.

    When the server generated the text (``yaml_proposal``) the record says so and ``verifiedAgainstRecommendation``
    is true: the text is exactly what the engine derives from the evaluation, and it is generated again from a
    fresh evaluation when the request is decided. Otherwise the YAML is supplied by the client, nothing checks
    that it matches the recommendation, and the hash is an INTEGRITY check only: it shows the stored text is
    unchanged since the request, not that it does what the recommendation says. That is stated in the record so
    it cannot be read as verification. Approving applies nothing, whatever the YAML says.
    """
    return {"sha256": _sha256(yaml_snapshot), "origin": SERVER_GENERATED if server_generated else CLIENT_SUPPLIED,
            "verifiedAgainstRecommendation": server_generated}


# Approving a candidate that is not the top-ranked one needs a written reason of at least this many characters.
MIN_JUSTIFICATION_CHARS = 20


class StaleProposalError(EvidenceError):
    """The proposal an approval was requested for is not what the current evaluation recommends any more."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            "APPROVAL_PROPOSAL_STALE",
            f"{detail} Reject this request and request a new approval from the current recommendation.",
            http_status=409,
            component="recommendation",
        )


class ApprovalService:
    """The approval gate.

    A candidate can be put forward, and later approved, only when the server's own evaluation says it is
    verified feasible and ranked with a positive score (``eligibility.py``). The evaluation is the one passed in
    when the request is made and a fresh one when it is decided, so a request written before this policy, or
    one the cluster has since overtaken, is judged by what is true now. The request is also bound to the exact
    proposal (``eligibility.proposal_fingerprint``): if the plan or its predicted effect has changed since it was
    requested, it is refused as stale. The top-ranked candidate is the automatic selection; any other ranked
    candidate needs a documented justification in the approval note.
    """

    # What this gate trusts. A subclass built for a narrower, explicitly named purpose (``research.py``) may change these two
    # hooks; the production gate trusts live evidence only and refuses a proposal the engine marks as not applicable.
    require_applicable_yaml = True

    def __init__(self, repository: ApprovalRepository, min_justification_chars: int = MIN_JUSTIFICATION_CHARS):
        self.repository = repository
        self.min_justification_chars = min_justification_chars

    def _require_provenance(self, approval: ApprovalModel) -> None:
        if not has_verified_provenance(approval):
            raise UnverifiedProvenanceError()

    def request_approval(
        self,
        recommendation_id: str,
        proposal_version: str,
        recommendation: RecommendationReport,
        yaml_snapshot: str | None = None,
        requested_by: str = "developer",
        evidence: dict | None = None,
        proposal_yaml: str | None = None,
    ) -> ApprovalModel:
        """Record a request for approval.

        ``proposal_yaml`` is the YAML the server generated for this candidate from the live evaluation, or None when
        none could be (the candidate is not eligible, or the evidence is not live). When it is given it is the YAML
        of the request: a client-supplied snapshot is accepted only if it is identical.
        """
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

        if proposal_yaml is not None:
            if yaml_snapshot is not None and yaml_snapshot != proposal_yaml:
                raise PreconditionError("The YAML sent with this request is not the YAML the server generates for "
                                        "this recommendation; send none to use the server's, or the identical text.")
            yaml_snapshot = proposal_yaml

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
        evaluation_snapshot["proposal"] = {
            "fingerprint": standing.fingerprint,
            "failedNode": (evidence or {}).get("failedNode"),
            "topologySnapshotId": (evidence or {}).get("topologySnapshotId"),   # for audit; not part of the binding
            "yaml": _yaml_record(yaml_snapshot, server_generated=proposal_yaml is not None),
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

        self._require_provenance(approval)

        if evaluate is None:
            raise PreconditionError("There is no current evaluation to check this candidate against, so it "
                                    "cannot be approved")
        current = evaluate(approval)
        standing = evaluate_eligibility(current, recommendation_id)
        if not standing.eligible:
            raise PreconditionError(standing.reason)
        bound = ((approval.evaluation_snapshot or {}).get("proposal") or {}).get("fingerprint")
        if bound is None:
            raise StaleProposalError("This approval request predates proposal binding, so it cannot be shown to be "
                                     "for what the current evaluation recommends.")
        if bound != standing.fingerprint:
            raise StaleProposalError("The plan or the predicted effect of this proposal has changed since it was "
                                     "requested (the topology moved), so it is not what the developer was shown.")
        recorded_yaml = ((approval.evaluation_snapshot or {}).get("proposal") or {}).get("yaml") or {}
        if recorded_yaml.get("sha256") != _sha256(approval.yaml_snapshot):
            raise StaleProposalError("The YAML stored with this request is not the YAML it was requested with "
                                     "(an integrity check only: the YAML is client-supplied and is not verified "
                                     "against the recommendation).")
        yaml_verified = self._check_generated_yaml(approval, current, recommendation_id, self.require_applicable_yaml)
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
            **standing.to_dict(), "proposalFingerprint": standing.fingerprint,
            "yamlVerifiedAgainstRecommendation": yaml_verified, "appliesNothing": True, "checkedAt": decided_at.isoformat(),
            "justification": justification if standing.requires_justification else None}}

        return self.repository.update(approval)

    @staticmethod
    def _check_generated_yaml(approval: ApprovalModel, current: LiveRecommendation, recommendation_id: str,
                              require_applicable: bool = True) -> bool:
        """For a request whose YAML the server generated: the server must generate the same text again, now.

        Returns whether the YAML was verified (False for a client-supplied snapshot, which is never verified).
        """
        recorded = ((approval.evaluation_snapshot or {}).get("proposal") or {}).get("yaml") or {}
        if recorded.get("origin") != SERVER_GENERATED:
            return False
        # Imported here: yaml_proposal builds on this package (eligibility), so a module-level import would be circular.
        from app.engine.recommendation_evaluation_validation.yaml_proposal import GENERATED, build_yaml_proposal
        regenerated = build_yaml_proposal(current, recommendation_id)
        if (regenerated.status != GENERATED or (require_applicable and not regenerated.applicable)
                or regenerated.sha256 != recorded.get("sha256")):
            raise StaleProposalError("The YAML the server generates for this recommendation now differs from the YAML "
                                     "it was requested with (the Deployment or its labels changed), so it is not what "
                                     "the developer was shown.")
        return True

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
