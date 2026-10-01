"""Versioned scenario storage ports and adapters for Component 1."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.engine.workflow_dependency_modeling.models import WorkflowScenario
from app.shared.persistence.models import ResearchArtifact


class ScenarioNotFoundError(LookupError):
    pass


class ScenarioAlreadyExistsError(ValueError):
    pass


class ScenarioVersionConflictError(ValueError):
    pass


class ScenarioRepository(Protocol):
    def list_latest(self) -> list[WorkflowScenario]: ...
    def get(self, workflow_id: str, version: int | None = None) -> WorkflowScenario: ...
    def create(self, scenario: WorkflowScenario) -> WorkflowScenario: ...
    def add_version(
        self, scenario: WorkflowScenario, expected_version: int
    ) -> WorkflowScenario: ...


class InMemoryScenarioRepository:
    """Thread-safe adapter for tests and database-free research execution."""

    def __init__(self) -> None:
        self._records: dict[str, list[WorkflowScenario]] = {}
        self._lock = RLock()

    def list_latest(self) -> list[WorkflowScenario]:
        with self._lock:
            return [self._records[key][-1] for key in sorted(self._records)]

    def get(self, workflow_id: str, version: int | None = None) -> WorkflowScenario:
        with self._lock:
            records = self._records.get(workflow_id, [])
            if not records:
                raise ScenarioNotFoundError(f"Workflow scenario '{workflow_id}' was not found")
            if version is None:
                return records[-1]
            for record in records:
                if record.version == version:
                    return record
        raise ScenarioNotFoundError(
            f"Workflow scenario '{workflow_id}' version {version} was not found"
        )

    def create(self, scenario: WorkflowScenario) -> WorkflowScenario:
        with self._lock:
            if scenario.workflow_id in self._records:
                raise ScenarioAlreadyExistsError(
                    f"Workflow scenario '{scenario.workflow_id}' already exists"
                )
            if scenario.version != 1:
                raise ScenarioVersionConflictError("A new scenario must start at version 1")
            self._records[scenario.workflow_id] = [scenario]
        return scenario

    def add_version(
        self, scenario: WorkflowScenario, expected_version: int
    ) -> WorkflowScenario:
        with self._lock:
            current = self.get(scenario.workflow_id)
            if current.version != expected_version:
                raise ScenarioVersionConflictError(
                    f"Expected version {expected_version}, current version is {current.version}"
                )
            if scenario.version != current.version + 1:
                raise ScenarioVersionConflictError("Scenario versions must increase by exactly one")
            self._records[scenario.workflow_id].append(scenario)
        return scenario


class SqlAlchemyScenarioRepository:
    """Persistent adapter using the team's dialect-neutral artifact table."""

    _PREFIX = "workflow_scenario:"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def _artifact_type(cls, workflow_id: str) -> str:
        return f"{cls._PREFIX}{workflow_id}"

    @staticmethod
    def _scenario(artifact: ResearchArtifact) -> WorkflowScenario:
        return WorkflowScenario.from_mapping(artifact.payload)

    def _records(self, session: Session, workflow_id: str) -> list[WorkflowScenario]:
        statement = (
            select(ResearchArtifact)
            .where(ResearchArtifact.artifact_type == self._artifact_type(workflow_id))
            .order_by(ResearchArtifact.id)
        )
        return [self._scenario(item) for item in session.scalars(statement)]

    def list_latest(self) -> list[WorkflowScenario]:
        statement = (
            select(ResearchArtifact)
            .where(ResearchArtifact.artifact_type.like(f"{self._PREFIX}%"))
            .order_by(ResearchArtifact.id)
        )
        latest: dict[str, WorkflowScenario] = {}
        with Session(self._engine) as session:
            for artifact in session.scalars(statement):
                scenario = self._scenario(artifact)
                current = latest.get(scenario.workflow_id)
                if current is None or scenario.version > current.version:
                    latest[scenario.workflow_id] = scenario
        return [latest[key] for key in sorted(latest)]

    def get(self, workflow_id: str, version: int | None = None) -> WorkflowScenario:
        with Session(self._engine) as session:
            records = self._records(session, workflow_id)
        if not records:
            raise ScenarioNotFoundError(f"Workflow scenario '{workflow_id}' was not found")
        if version is None:
            return max(records, key=lambda item: item.version)
        try:
            return next(item for item in records if item.version == version)
        except StopIteration as err:
            raise ScenarioNotFoundError(
                f"Workflow scenario '{workflow_id}' version {version} was not found"
            ) from err

    def create(self, scenario: WorkflowScenario) -> WorkflowScenario:
        if scenario.version != 1:
            raise ScenarioVersionConflictError("A new scenario must start at version 1")
        with Session(self._engine) as session, session.begin():
            if self._records(session, scenario.workflow_id):
                raise ScenarioAlreadyExistsError(
                    f"Workflow scenario '{scenario.workflow_id}' already exists"
                )
            session.add(
                ResearchArtifact(
                    artifact_type=self._artifact_type(scenario.workflow_id),
                    payload=scenario.to_mapping(),
                )
            )
        return scenario

    def add_version(
        self, scenario: WorkflowScenario, expected_version: int
    ) -> WorkflowScenario:
        with Session(self._engine) as session, session.begin():
            records = self._records(session, scenario.workflow_id)
            if not records:
                raise ScenarioNotFoundError(
                    f"Workflow scenario '{scenario.workflow_id}' was not found"
                )
            current = max(records, key=lambda item: item.version)
            if current.version != expected_version:
                raise ScenarioVersionConflictError(
                    f"Expected version {expected_version}, current version is {current.version}"
                )
            if scenario.version != current.version + 1:
                raise ScenarioVersionConflictError("Scenario versions must increase by exactly one")
            session.add(
                ResearchArtifact(
                    artifact_type=self._artifact_type(scenario.workflow_id),
                    payload=scenario.to_mapping(),
                )
            )
        return scenario
