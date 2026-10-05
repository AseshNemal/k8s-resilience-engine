"""Append-only intervention evidence contract tests."""

from datetime import datetime, timezone

from sqlalchemy import create_engine

from app.engine.workflow_dependency_modeling.intervention_event_models import (
    InterventionEvent,
)
from app.engine.workflow_dependency_modeling.intervention_event_repository import (
    InMemoryInterventionEventRepository,
    SqlAlchemyInterventionEventRepository,
)
from app.shared.persistence.models import Base


def _event(event_id: str, event_type: str, occurred_at: str) -> InterventionEvent:
    return InterventionEvent.from_mapping(
        {
            "eventId": event_id,
            "campaignId": "campaign:1",
            "targetService": "paymentservice",
            "batchId": "batch:intervention:paymentservice",
            "eventType": event_type,
            "evidence": f"operator-observation:{event_type}",
            "occurredAt": occurred_at,
            "recordedAt": occurred_at,
            "recordedBy": "operator",
        }
    )


def test_event_round_trip_preserves_operator_evidence() -> None:
    event = _event("event:1", "activated", "2026-10-05T01:00:00Z")
    assert InterventionEvent.from_mapping(event.to_mapping()) == event
    assert event.event_type.value == "activated"
    assert event.evidence == "operator-observation:activated"


def test_repositories_keep_events_append_only_and_chronological(tmp_path) -> None:
    activated = _event("event:1", "activated", "2026-10-05T01:00:00Z")
    restored = _event("event:2", "restored", "2026-10-05T01:05:00Z")
    memory = InMemoryInterventionEventRepository()
    memory.create(restored)
    memory.create(activated)
    assert memory.list("campaign:1", "paymentservice") == [activated, restored]

    engine = create_engine(f"sqlite:///{tmp_path / 'interventions.db'}")
    Base.metadata.create_all(engine)
    database = SqlAlchemyInterventionEventRepository(engine)
    database.create(restored)
    database.create(activated)
    reopened = SqlAlchemyInterventionEventRepository(engine)
    assert reopened.list("campaign:1") == [activated, restored]
