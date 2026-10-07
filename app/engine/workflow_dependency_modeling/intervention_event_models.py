"""Append-only operator evidence for controlled service interventions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import ComponentDataError


class InterventionEventType(str, Enum):
    ACTIVATED = "activated"
    RESTORED = "restored"


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"Intervention event requires non-empty '{key}'")
    result = value.strip()
    if len(result) > 512:
        raise ComponentDataError(f"Intervention event '{key}' is too long")
    return result


def _timestamp(value: Any, key: str) -> datetime:
    if not isinstance(value, str):
        raise ComponentDataError(f"Intervention event '{key}' must be ISO 8601")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ComponentDataError(f"Intervention event '{key}' must be ISO 8601") from err
    if result.tzinfo is None:
        raise ComponentDataError(f"Intervention event '{key}' must include a timezone")
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class InterventionEvent:
    event_id: str
    campaign_id: str
    target_service: str
    batch_id: str
    event_type: InterventionEventType
    evidence: str
    occurred_at: datetime
    recorded_at: datetime
    recorded_by: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InterventionEvent":
        try:
            event_type = InterventionEventType(data.get("eventType"))
        except ValueError as err:
            raise ComponentDataError("Intervention event type must be activated or restored") from err
        return cls(
            event_id=_text(data, "eventId"),
            campaign_id=_text(data, "campaignId"),
            target_service=_text(data, "targetService"),
            batch_id=_text(data, "batchId"),
            event_type=event_type,
            evidence=_text(data, "evidence"),
            occurred_at=_timestamp(data.get("occurredAt"), "occurredAt"),
            recorded_at=_timestamp(data.get("recordedAt"), "recordedAt"),
            recorded_by=_text(data, "recordedBy"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "campaignId": self.campaign_id,
            "targetService": self.target_service,
            "batchId": self.batch_id,
            "eventType": self.event_type.value,
            "evidence": self.evidence,
            "occurredAt": _iso(self.occurred_at),
            "recordedAt": _iso(self.recorded_at),
            "recordedBy": self.recorded_by,
        }
