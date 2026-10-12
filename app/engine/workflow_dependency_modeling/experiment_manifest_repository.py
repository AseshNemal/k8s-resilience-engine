"""Persistence ports for immutable Component 1 experiment manifests."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.engine.workflow_dependency_modeling.experiment_manifest_models import (
    ExperimentManifest,
)
from app.shared.persistence.models import ResearchArtifact


class ExperimentManifestNotFoundError(LookupError):
    pass


class ExperimentManifestConflictError(ValueError):
    pass


class ExperimentManifestRepository(Protocol):
    def create(self, manifest: ExperimentManifest) -> ExperimentManifest: ...
    def get(self, manifest_id: str) -> ExperimentManifest: ...
    def list(self) -> list[ExperimentManifest]: ...


class InMemoryExperimentManifestRepository:
    def __init__(self) -> None:
        self._manifests: dict[str, ExperimentManifest] = {}
        self._lock = RLock()

    def create(self, manifest: ExperimentManifest) -> ExperimentManifest:
        with self._lock:
            if manifest.manifest_id in self._manifests:
                raise ExperimentManifestConflictError(
                    f"Experiment manifest '{manifest.manifest_id}' already exists"
                )
            self._manifests[manifest.manifest_id] = manifest
        return manifest

    def get(self, manifest_id: str) -> ExperimentManifest:
        with self._lock:
            try:
                return self._manifests[manifest_id]
            except KeyError as err:
                raise ExperimentManifestNotFoundError(
                    f"Experiment manifest '{manifest_id}' was not found"
                ) from err

    def list(self) -> list[ExperimentManifest]:
        with self._lock:
            return sorted(
                self._manifests.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )


class SqlAlchemyExperimentManifestRepository:
    _PREFIX = "workflow_experiment_manifest:"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def _artifact_type(cls, manifest_id: str) -> str:
        return f"{cls._PREFIX}{manifest_id}"

    def create(self, manifest: ExperimentManifest) -> ExperimentManifest:
        artifact_type = self._artifact_type(manifest.manifest_id)
        with Session(self._engine) as database, database.begin():
            existing = database.scalar(
                select(ResearchArtifact.id).where(
                    ResearchArtifact.artifact_type == artifact_type
                )
            )
            if existing is not None:
                raise ExperimentManifestConflictError(
                    f"Experiment manifest '{manifest.manifest_id}' already exists"
                )
            database.add(
                ResearchArtifact(
                    artifact_type=artifact_type,
                    payload=manifest.to_mapping(),
                )
            )
        return manifest

    def get(self, manifest_id: str) -> ExperimentManifest:
        with Session(self._engine) as database:
            artifact = database.scalar(
                select(ResearchArtifact).where(
                    ResearchArtifact.artifact_type == self._artifact_type(manifest_id)
                )
            )
        if artifact is None:
            raise ExperimentManifestNotFoundError(
                f"Experiment manifest '{manifest_id}' was not found"
            )
        return ExperimentManifest.from_mapping(artifact.payload)

    def list(self) -> list[ExperimentManifest]:
        statement = (
            select(ResearchArtifact)
            .where(ResearchArtifact.artifact_type.like(f"{self._PREFIX}%"))
            .order_by(ResearchArtifact.id.desc())
        )
        with Session(self._engine) as database:
            return [
                ExperimentManifest.from_mapping(item.payload)
                for item in database.scalars(statement)
            ]
