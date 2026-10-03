from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.shared.schemas.approval import ApprovalStatus

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
