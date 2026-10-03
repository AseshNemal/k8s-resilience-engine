from enum import Enum
from pydantic import BaseModel
from typing import Any, Dict, Optional
from datetime import datetime
from app.shared.schemas.base import APIModel
from app.shared.schemas.recommendation import RecommendationCandidate

class ApprovalStatus(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class ApprovalRequest(APIModel):
    recommendation_id: str
    proposal_version: str
    yaml_snapshot: str | None = None

class ApprovalDecisionRequest(APIModel):
    note: str
    expected_proposal_version: str

class ApprovalResponse(APIModel):
    id: int
    recommendation_id: str
    proposal_version: str
    status: ApprovalStatus
    requested_at: datetime
    requested_by: str
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_note: str | None = None
    recommendation_snapshot: dict
    yaml_snapshot: str | None = None
    evaluation_snapshot: dict | None = None
