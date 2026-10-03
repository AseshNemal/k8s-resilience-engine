"""Persistence ports for guided workflow-analysis sessions."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.engine.workflow_dependency_modeling.analysis_session_models import (
    WorkflowAnalysisSession,
)
from app.shared.persistence.models import ResearchArtifact


class AnalysisSessionNotFoundError(LookupError):
    pass


class AnalysisSessionConflictError(ValueError):
    pass


class AnalysisSessionRepository(Protocol):
    def create(self, session: WorkflowAnalysisSession) -> WorkflowAnalysisSession: ...
    def get(self, session_id: str) -> WorkflowAnalysisSession: ...
    def list(self) -> list[WorkflowAnalysisSession]: ...


class InMemoryAnalysisSessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, WorkflowAnalysisSession] = {}
        self._lock = RLock()

    def create(self, session: WorkflowAnalysisSession) -> WorkflowAnalysisSession:
        with self._lock:
            if session.session_id in self._sessions:
                raise AnalysisSessionConflictError(
                    f"Analysis session '{session.session_id}' already exists"
                )
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> WorkflowAnalysisSession:
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as err:
                raise AnalysisSessionNotFoundError(
                    f"Analysis session '{session_id}' was not found"
                ) from err

    def list(self) -> list[WorkflowAnalysisSession]:
        with self._lock:
            return sorted(
                self._sessions.values(), key=lambda item: item.created_at, reverse=True
            )


class SqlAlchemyAnalysisSessionRepository:
    _PREFIX = "workflow_analysis_session:"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def _artifact_type(cls, session_id: str) -> str:
        return f"{cls._PREFIX}{session_id}"

    def create(self, analysis: WorkflowAnalysisSession) -> WorkflowAnalysisSession:
        artifact_type = self._artifact_type(analysis.session_id)
        with Session(self._engine) as database, database.begin():
            existing = database.scalar(
                select(ResearchArtifact.id).where(
                    ResearchArtifact.artifact_type == artifact_type
                )
            )
            if existing is not None:
                raise AnalysisSessionConflictError(
                    f"Analysis session '{analysis.session_id}' already exists"
                )
            database.add(
                ResearchArtifact(
                    artifact_type=artifact_type,
                    payload=analysis.to_mapping(),
                )
            )
        return analysis

    def get(self, session_id: str) -> WorkflowAnalysisSession:
        with Session(self._engine) as database:
            artifact = database.scalar(
                select(ResearchArtifact).where(
                    ResearchArtifact.artifact_type == self._artifact_type(session_id)
                )
            )
        if artifact is None:
            raise AnalysisSessionNotFoundError(
                f"Analysis session '{session_id}' was not found"
            )
        return WorkflowAnalysisSession.from_mapping(artifact.payload)

    def list(self) -> list[WorkflowAnalysisSession]:
        statement = (
            select(ResearchArtifact)
            .where(ResearchArtifact.artifact_type.like(f"{self._PREFIX}%"))
            .order_by(ResearchArtifact.id.desc())
        )
        with Session(self._engine) as database:
            return [
                WorkflowAnalysisSession.from_mapping(item.payload)
                for item in database.scalars(statement)
            ]
