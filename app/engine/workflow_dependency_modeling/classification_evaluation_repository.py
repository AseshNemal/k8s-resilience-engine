"""Persistence ports for classification references and evaluation runs."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.engine.workflow_dependency_modeling.classification_evaluation_models import (
    ClassificationEvaluation,
    DependencyClassificationReference,
)
from app.shared.persistence.models import ResearchArtifact


class ClassificationArtifactNotFoundError(LookupError):
    pass


class ClassificationArtifactConflictError(ValueError):
    pass


class ClassificationEvaluationRepository(Protocol):
    def add_reference(
        self,
        reference: DependencyClassificationReference,
        expected_version: int,
    ) -> DependencyClassificationReference: ...

    def get_reference(
        self, workflow_id: str, version: int | None = None
    ) -> DependencyClassificationReference: ...

    def list_references(self) -> list[DependencyClassificationReference]: ...

    def create_evaluation(
        self, evaluation: ClassificationEvaluation
    ) -> ClassificationEvaluation: ...

    def get_evaluation(self, evaluation_id: str) -> ClassificationEvaluation: ...

    def list_evaluations(
        self, workflow_id: str | None = None
    ) -> list[ClassificationEvaluation]: ...


class InMemoryClassificationEvaluationRepository:
    def __init__(self) -> None:
        self._references: dict[str, list[DependencyClassificationReference]] = {}
        self._evaluations: dict[str, ClassificationEvaluation] = {}
        self._lock = RLock()

    def add_reference(
        self,
        reference: DependencyClassificationReference,
        expected_version: int,
    ) -> DependencyClassificationReference:
        with self._lock:
            records = self._references.setdefault(reference.workflow_id, [])
            current_version = records[-1].reference_version if records else 0
            if current_version != expected_version:
                raise ClassificationArtifactConflictError(
                    f"Expected reference version {expected_version}, current version is {current_version}"
                )
            if reference.reference_version != current_version + 1:
                raise ClassificationArtifactConflictError(
                    "Reference versions must increase by exactly one"
                )
            records.append(reference)
        return reference

    def get_reference(
        self, workflow_id: str, version: int | None = None
    ) -> DependencyClassificationReference:
        with self._lock:
            records = list(self._references.get(workflow_id, []))
        if not records:
            raise ClassificationArtifactNotFoundError(
                f"Dependency reference for workflow '{workflow_id}' was not found"
            )
        if version is None:
            return records[-1]
        try:
            return next(item for item in records if item.reference_version == version)
        except StopIteration as err:
            raise ClassificationArtifactNotFoundError(
                f"Dependency reference for workflow '{workflow_id}' version {version} was not found"
            ) from err

    def list_references(self) -> list[DependencyClassificationReference]:
        with self._lock:
            return [self._references[key][-1] for key in sorted(self._references)]

    def create_evaluation(
        self, evaluation: ClassificationEvaluation
    ) -> ClassificationEvaluation:
        with self._lock:
            if evaluation.evaluation_id in self._evaluations:
                raise ClassificationArtifactConflictError(
                    f"Classification evaluation '{evaluation.evaluation_id}' already exists"
                )
            self._evaluations[evaluation.evaluation_id] = evaluation
        return evaluation

    def get_evaluation(self, evaluation_id: str) -> ClassificationEvaluation:
        with self._lock:
            try:
                return self._evaluations[evaluation_id]
            except KeyError as err:
                raise ClassificationArtifactNotFoundError(
                    f"Classification evaluation '{evaluation_id}' was not found"
                ) from err

    def list_evaluations(
        self, workflow_id: str | None = None
    ) -> list[ClassificationEvaluation]:
        with self._lock:
            records = list(self._evaluations.values())
        selected = [
            item for item in records if workflow_id is None or item.workflow_id == workflow_id
        ]
        return sorted(selected, key=lambda item: item.created_at, reverse=True)


class SqlAlchemyClassificationEvaluationRepository:
    _REFERENCE_PREFIX = "workflow_dependency_reference:"
    _EVALUATION_PREFIX = "workflow_classification_evaluation:"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def _reference_type(cls, workflow_id: str) -> str:
        return f"{cls._REFERENCE_PREFIX}{workflow_id}"

    @classmethod
    def _evaluation_type(cls, evaluation_id: str) -> str:
        return f"{cls._EVALUATION_PREFIX}{evaluation_id}"

    def _reference_records(
        self, session: Session, workflow_id: str
    ) -> list[DependencyClassificationReference]:
        statement = (
            select(ResearchArtifact)
            .where(ResearchArtifact.artifact_type == self._reference_type(workflow_id))
            .order_by(ResearchArtifact.id)
        )
        return [
            DependencyClassificationReference.from_mapping(item.payload)
            for item in session.scalars(statement)
        ]

    def add_reference(
        self,
        reference: DependencyClassificationReference,
        expected_version: int,
    ) -> DependencyClassificationReference:
        with Session(self._engine) as session, session.begin():
            records = self._reference_records(session, reference.workflow_id)
            current_version = records[-1].reference_version if records else 0
            if current_version != expected_version:
                raise ClassificationArtifactConflictError(
                    f"Expected reference version {expected_version}, current version is {current_version}"
                )
            if reference.reference_version != current_version + 1:
                raise ClassificationArtifactConflictError(
                    "Reference versions must increase by exactly one"
                )
            session.add(
                ResearchArtifact(
                    artifact_type=self._reference_type(reference.workflow_id),
                    payload=reference.to_mapping(),
                )
            )
        return reference

    def get_reference(
        self, workflow_id: str, version: int | None = None
    ) -> DependencyClassificationReference:
        with Session(self._engine) as session:
            records = self._reference_records(session, workflow_id)
        if not records:
            raise ClassificationArtifactNotFoundError(
                f"Dependency reference for workflow '{workflow_id}' was not found"
            )
        if version is None:
            return records[-1]
        try:
            return next(item for item in records if item.reference_version == version)
        except StopIteration as err:
            raise ClassificationArtifactNotFoundError(
                f"Dependency reference for workflow '{workflow_id}' version {version} was not found"
            ) from err

    def list_references(self) -> list[DependencyClassificationReference]:
        statement = select(ResearchArtifact).where(
            ResearchArtifact.artifact_type.like(f"{self._REFERENCE_PREFIX}%")
        )
        latest: dict[str, DependencyClassificationReference] = {}
        with Session(self._engine) as session:
            for artifact in session.scalars(statement):
                reference = DependencyClassificationReference.from_mapping(artifact.payload)
                current = latest.get(reference.workflow_id)
                if current is None or reference.reference_version > current.reference_version:
                    latest[reference.workflow_id] = reference
        return [latest[key] for key in sorted(latest)]

    def create_evaluation(
        self, evaluation: ClassificationEvaluation
    ) -> ClassificationEvaluation:
        artifact_type = self._evaluation_type(evaluation.evaluation_id)
        with Session(self._engine) as session, session.begin():
            existing = session.scalar(
                select(ResearchArtifact.id).where(
                    ResearchArtifact.artifact_type == artifact_type
                )
            )
            if existing is not None:
                raise ClassificationArtifactConflictError(
                    f"Classification evaluation '{evaluation.evaluation_id}' already exists"
                )
            session.add(
                ResearchArtifact(
                    artifact_type=artifact_type,
                    payload=evaluation.to_mapping(),
                )
            )
        return evaluation

    def get_evaluation(self, evaluation_id: str) -> ClassificationEvaluation:
        with Session(self._engine) as session:
            artifact = session.scalar(
                select(ResearchArtifact).where(
                    ResearchArtifact.artifact_type == self._evaluation_type(evaluation_id)
                )
            )
        if artifact is None:
            raise ClassificationArtifactNotFoundError(
                f"Classification evaluation '{evaluation_id}' was not found"
            )
        return ClassificationEvaluation.from_mapping(artifact.payload)

    def list_evaluations(
        self, workflow_id: str | None = None
    ) -> list[ClassificationEvaluation]:
        statement = select(ResearchArtifact).where(
            ResearchArtifact.artifact_type.like(f"{self._EVALUATION_PREFIX}%")
        )
        with Session(self._engine) as session:
            records = [
                ClassificationEvaluation.from_mapping(item.payload)
                for item in session.scalars(statement)
            ]
        selected = [
            item for item in records if workflow_id is None or item.workflow_id == workflow_id
        ]
        return sorted(selected, key=lambda item: item.created_at, reverse=True)
