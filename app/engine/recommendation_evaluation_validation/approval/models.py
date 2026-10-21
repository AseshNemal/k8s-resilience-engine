from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.shared.schemas.approval import ApprovalStatus
from app.shared.schemas.recommendation import RecommendationReport

# The only provenance marker that makes an approval trusted. It is written by the server from the
# live recommendation source; a client can never supply it.
LIVE_EVIDENCE_SOURCE = "live-discovery"

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


def has_verified_provenance(approval: "ApprovalModel") -> bool:
    provenance = (approval.evaluation_snapshot or {}).get("provenance")
    return isinstance(provenance, dict) and provenance.get("source") == LIVE_EVIDENCE_SOURCE
