"""Persistence ports for append-only intervention evidence."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.engine.workflow_dependency_modeling.intervention_event_models import (
    InterventionEvent,
)
from app.shared.persistence.models import ResearchArtifact


class InterventionEventConflictError(ValueError):
    pass


class InterventionEventRepository(Protocol):
    def create(self, event: InterventionEvent) -> InterventionEvent: ...
    def list(
        self,
        campaign_id: str,
        target_service: str | None = None,
    ) -> list[InterventionEvent]: ...


def _filter(
    events: list[InterventionEvent],
    campaign_id: str,
    target_service: str | None,
) -> list[InterventionEvent]:
    return sorted(
        (
            event
            for event in events
            if event.campaign_id == campaign_id
            and (target_service is None or event.target_service == target_service)
        ),
        key=lambda event: (event.occurred_at, event.recorded_at, event.event_id),
    )


class InMemoryInterventionEventRepository:
    def __init__(self) -> None:
        self._events: dict[str, InterventionEvent] = {}
        self._lock = RLock()

    def create(self, event: InterventionEvent) -> InterventionEvent:
        with self._lock:
            if event.event_id in self._events:
                raise InterventionEventConflictError(
                    f"Intervention event '{event.event_id}' already exists"
                )
            self._events[event.event_id] = event
        return event

    def list(
        self,
        campaign_id: str,
        target_service: str | None = None,
    ) -> list[InterventionEvent]:
        with self._lock:
            return _filter(list(self._events.values()), campaign_id, target_service)


class SqlAlchemyInterventionEventRepository:
    _PREFIX = "workflow_intervention_event:"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def _artifact_type(cls, event_id: str) -> str:
        return f"{cls._PREFIX}{event_id}"

    def create(self, event: InterventionEvent) -> InterventionEvent:
        artifact_type = self._artifact_type(event.event_id)
        with Session(self._engine) as database, database.begin():
            existing = database.scalar(
                select(ResearchArtifact.id).where(
                    ResearchArtifact.artifact_type == artifact_type
                )
            )
            if existing is not None:
                raise InterventionEventConflictError(
                    f"Intervention event '{event.event_id}' already exists"
                )
            database.add(
                ResearchArtifact(artifact_type=artifact_type, payload=event.to_mapping())
            )
        return event

    def list(
        self,
        campaign_id: str,
        target_service: str | None = None,
    ) -> list[InterventionEvent]:
        statement = select(ResearchArtifact).where(
            ResearchArtifact.artifact_type.like(f"{self._PREFIX}%")
        )
        with Session(self._engine) as database:
            events = [
                InterventionEvent.from_mapping(item.payload)
                for item in database.scalars(statement)
            ]
        return _filter(events, campaign_id, target_service)
