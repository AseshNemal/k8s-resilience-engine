"""Persistence ports for frozen workflow graph handoffs."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.engine.workflow_dependency_modeling.graph_freeze_models import FrozenWorkflowGraph
from app.shared.persistence.models import ResearchArtifact


class FrozenGraphNotFoundError(LookupError):
    pass


class FrozenGraphAlreadyExistsError(ValueError):
    pass


class GraphFreezeRepository(Protocol):
    def create(self, artifact: FrozenWorkflowGraph) -> FrozenWorkflowGraph: ...
    def latest(self, workflow_id: str) -> FrozenWorkflowGraph: ...


class InMemoryGraphFreezeRepository:
    def __init__(self) -> None:
        self._records: dict[str, FrozenWorkflowGraph] = {}
        self._lock = RLock()

    def create(self, artifact: FrozenWorkflowGraph) -> FrozenWorkflowGraph:
        with self._lock:
            if artifact.freeze_id in self._records:
                raise FrozenGraphAlreadyExistsError(
                    f"Frozen workflow graph '{artifact.freeze_id}' already exists"
                )
            self._records[artifact.freeze_id] = artifact
        return artifact

    def latest(self, workflow_id: str) -> FrozenWorkflowGraph:
        with self._lock:
            records = [
                item for item in self._records.values() if item.workflow_id == workflow_id
            ]
        if not records:
            raise FrozenGraphNotFoundError(
                f"No frozen workflow graph exists for workflow '{workflow_id}'"
            )
        return max(records, key=lambda item: (item.frozen_at, item.freeze_id))


class SqlAlchemyGraphFreezeRepository:
    _PREFIX = "workflow_graph_freeze:"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def _artifact_type(cls, freeze_id: str) -> str:
        return f"{cls._PREFIX}{freeze_id}"

    def create(self, artifact: FrozenWorkflowGraph) -> FrozenWorkflowGraph:
        artifact_type = self._artifact_type(artifact.freeze_id)
        with Session(self._engine) as session, session.begin():
            existing = session.scalar(
                select(ResearchArtifact.id).where(
                    ResearchArtifact.artifact_type == artifact_type
                )
            )
            if existing is not None:
                raise FrozenGraphAlreadyExistsError(
                    f"Frozen workflow graph '{artifact.freeze_id}' already exists"
                )
            session.add(
                ResearchArtifact(
                    artifact_type=artifact_type,
                    payload=artifact.to_mapping(),
                )
            )
        return artifact

    def latest(self, workflow_id: str) -> FrozenWorkflowGraph:
        statement = select(ResearchArtifact).where(
            ResearchArtifact.artifact_type.like(f"{self._PREFIX}%")
        )
        with Session(self._engine) as session:
            records = [
                FrozenWorkflowGraph.from_mapping(item.payload)
                for item in session.scalars(statement)
            ]
        selected = [item for item in records if item.workflow_id == workflow_id]
        if not selected:
            raise FrozenGraphNotFoundError(
                f"No frozen workflow graph exists for workflow '{workflow_id}'"
            )
        return max(selected, key=lambda item: (item.frozen_at, item.freeze_id))
