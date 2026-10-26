"""Append-only storage for held-out validation: a run's frozen predictions, then one result per scenario.

Predictions and results are separate records written at different times, so a stored prediction can be shown to
predate the outcome it is scored against, and nothing written later can alter it.
"""

from __future__ import annotations

from threading import RLock
from typing import Any, Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.shared.persistence.models import ResearchArtifact


class HeldOutRecordConflictError(ValueError):
    pass


class HeldOutValidationRepository(Protocol):
    def create(self, key: str, payload: dict[str, Any]) -> None: ...
    def list(self, prefix: str) -> list[dict[str, Any]]: ...


class InMemoryHeldOutValidationRepository:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def create(self, key: str, payload: dict[str, Any]) -> None:
        with self._lock:
            if key in self._records:
                raise HeldOutRecordConflictError(f"Held-out record '{key}' already exists")
            self._records[key] = dict(payload)

    def list(self, prefix: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(value) for key, value in sorted(self._records.items()) if key.startswith(prefix)]


class SqlAlchemyHeldOutValidationRepository:
    _PREFIX = "workflow_held_out:"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, key: str, payload: dict[str, Any]) -> None:
        artifact_type = f"{self._PREFIX}{key}"
        with Session(self._engine) as database, database.begin():
            existing = database.scalar(select(ResearchArtifact.id).where(ResearchArtifact.artifact_type == artifact_type))
            if existing is not None:
                raise HeldOutRecordConflictError(f"Held-out record '{key}' already exists")
            database.add(ResearchArtifact(artifact_type=artifact_type, payload=payload))

    def list(self, prefix: str) -> list[dict[str, Any]]:
        statement = select(ResearchArtifact).where(ResearchArtifact.artifact_type.like(f"{self._PREFIX}{prefix}%"))
        with Session(self._engine) as database:
            return [dict(item.payload) for item in sorted(database.scalars(statement), key=lambda item: item.artifact_type)]
