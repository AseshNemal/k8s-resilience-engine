"""Persistence ports and adapters for immutable workflow execution outcomes."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.engine.workflow_dependency_modeling.execution_models import ExecutionOutcome
from app.shared.persistence.models import ResearchArtifact


class ExecutionNotFoundError(LookupError):
    pass


class ExecutionAlreadyExistsError(ValueError):
    pass


class ExecutionRepository(Protocol):
    def create(self, outcome: ExecutionOutcome) -> ExecutionOutcome: ...

    def get(self, execution_id: str) -> ExecutionOutcome: ...

    def list(
        self,
        workflow_id: str | None = None,
        batch_id: str | None = None,
    ) -> list[ExecutionOutcome]: ...


class InMemoryExecutionRepository:
    """Thread-safe adapter for unit tests and database-free research execution."""

    def __init__(self) -> None:
        self._records: dict[str, ExecutionOutcome] = {}
        self._lock = RLock()

    def create(self, outcome: ExecutionOutcome) -> ExecutionOutcome:
        execution_id = outcome.registration.execution_id
        with self._lock:
            if execution_id in self._records:
                raise ExecutionAlreadyExistsError(
                    f"Workflow execution '{execution_id}' already exists"
                )
            self._records[execution_id] = outcome
        return outcome

    def get(self, execution_id: str) -> ExecutionOutcome:
        with self._lock:
            try:
                return self._records[execution_id]
            except KeyError as err:
                raise ExecutionNotFoundError(
                    f"Workflow execution '{execution_id}' was not found"
                ) from err

    def list(
        self,
        workflow_id: str | None = None,
        batch_id: str | None = None,
    ) -> list[ExecutionOutcome]:
        with self._lock:
            records = list(self._records.values())
        return _filter_outcomes(records, workflow_id, batch_id)


def _filter_outcomes(
    records: list[ExecutionOutcome],
    workflow_id: str | None,
    batch_id: str | None,
) -> list[ExecutionOutcome]:
    filtered = [
        item
        for item in records
        if (workflow_id is None or item.registration.workflow_id == workflow_id)
        and (batch_id is None or item.registration.batch_id == batch_id)
    ]
    return sorted(
        filtered,
        key=lambda item: (item.recorded_at, item.registration.execution_id),
        reverse=True,
    )


class SqlAlchemyExecutionRepository:
    """Persistent adapter using the team's dialect-neutral artifact table."""

    _PREFIX = "workflow_execution:"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def _artifact_type(cls, execution_id: str) -> str:
        return f"{cls._PREFIX}{execution_id}"

    def create(self, outcome: ExecutionOutcome) -> ExecutionOutcome:
        artifact_type = self._artifact_type(outcome.registration.execution_id)
        with Session(self._engine) as session, session.begin():
            existing = session.scalar(
                select(ResearchArtifact.id).where(
                    ResearchArtifact.artifact_type == artifact_type
                )
            )
            if existing is not None:
                raise ExecutionAlreadyExistsError(
                    f"Workflow execution '{outcome.registration.execution_id}' already exists"
                )
            session.add(
                ResearchArtifact(
                    artifact_type=artifact_type,
                    payload=outcome.to_mapping(),
                )
            )
        return outcome

    def get(self, execution_id: str) -> ExecutionOutcome:
        with Session(self._engine) as session:
            artifact = session.scalar(
                select(ResearchArtifact).where(
                    ResearchArtifact.artifact_type == self._artifact_type(execution_id)
                )
            )
        if artifact is None:
            raise ExecutionNotFoundError(
                f"Workflow execution '{execution_id}' was not found"
            )
        return ExecutionOutcome.from_mapping(artifact.payload)

    def list(
        self,
        workflow_id: str | None = None,
        batch_id: str | None = None,
    ) -> list[ExecutionOutcome]:
        statement = (
            select(ResearchArtifact)
            .where(ResearchArtifact.artifact_type.like(f"{self._PREFIX}%"))
            .order_by(ResearchArtifact.id.desc())
        )
        with Session(self._engine) as session:
            records = [
                ExecutionOutcome.from_mapping(item.payload)
                for item in session.scalars(statement)
            ]
        return _filter_outcomes(records, workflow_id, batch_id)
