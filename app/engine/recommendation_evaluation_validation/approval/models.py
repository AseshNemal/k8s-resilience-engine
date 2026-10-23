from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.shared.schemas.approval import ApprovalStatus
from app.shared.schemas.recommendation import RecommendationReport

# The only provenance marker that makes an approval trusted. It is written by the server from the
# live recommendation source; a client can never supply it.
LIVE_EVIDENCE_SOURCE = "live-discovery"

# Live topology, but a workflow a person DECLARED instead of one Component 1 observed, approved and froze. It is shown and studied;
# it is never trusted: ``has_verified_provenance`` accepts only LIVE_EVIDENCE_SOURCE, so nothing computed from a declaration can be
# approved, applied or validated.
DECLARED_WORKFLOW_SOURCE = "declared-workflow"

@dataclass
class ApprovalModel:
    id: int
    recommendation_id: str
    proposal_version: str
    status: ApprovalStatus
    requested_at: datetime
    requested_by: str
    recommendation_snapshot: dict[str, Any]
    
    yaml_snapshot: str | None = None
    evaluation_snapshot: dict[str, Any] | None = None
    
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_note: str | None = None


@dataclass
class LiveRecommendation:
    """A recommendation report together with the live evidence it was computed from."""

    report: RecommendationReport
    evidence: dict[str, Any]
    # What the Recommendations page shows beside the report (root cause, current and simulated placement, ...).
    # It is display detail only: it is never stored with an approval and is not part of the proposal fingerprint,
    # so adding to it cannot change what an approval is bound to. Approvals read ``evidence`` and nothing else.
    details: dict[str, Any] = field(default_factory=dict)


def has_verified_provenance(approval: "ApprovalModel") -> bool:
    provenance = (approval.evaluation_snapshot or {}).get("provenance")
    return isinstance(provenance, dict) and provenance.get("source") == LIVE_EVIDENCE_SOURCE
