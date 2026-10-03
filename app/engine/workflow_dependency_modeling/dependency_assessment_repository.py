"""Persistence ports for immutable dependency assessments and approvals."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentApproval,
    DependencyAssessment,
)
from app.shared.persistence.models import ResearchArtifact


class DependencyAssessmentNotFoundError(LookupError):
    pass


class DependencyAssessmentAlreadyExistsError(ValueError):
    pass


class DependencyAssessmentNotApprovableError(ValueError):
    pass


class DependencyAssessmentRepository(Protocol):
    def create_assessment(
        self, assessment: DependencyAssessment
    ) -> DependencyAssessment: ...

    def get_assessment(self, assessment_id: str) -> DependencyAssessment: ...

    def list_assessments(
        self, workflow_id: str | None = None
    ) -> list[DependencyAssessment]: ...

    def create_approval(self, approval: AssessmentApproval) -> AssessmentApproval: ...

    def get_approval(self, assessment_id: str) -> AssessmentApproval: ...

    def latest_approval(self, workflow_id: str) -> AssessmentApproval: ...


class InMemoryDependencyAssessmentRepository:
    def __init__(self) -> None:
        self._assessments: dict[str, DependencyAssessment] = {}
        self._approvals: dict[str, AssessmentApproval] = {}
        self._lock = RLock()

    def create_assessment(
        self, assessment: DependencyAssessment
    ) -> DependencyAssessment:
        with self._lock:
            if assessment.assessment_id in self._assessments:
                raise DependencyAssessmentAlreadyExistsError(
                    f"Dependency assessment '{assessment.assessment_id}' already exists"
                )
            self._assessments[assessment.assessment_id] = assessment
        return assessment

    def get_assessment(self, assessment_id: str) -> DependencyAssessment:
        with self._lock:
            try:
                return self._assessments[assessment_id]
            except KeyError as err:
                raise DependencyAssessmentNotFoundError(
                    f"Dependency assessment '{assessment_id}' was not found"
                ) from err

    def list_assessments(
        self, workflow_id: str | None = None
    ) -> list[DependencyAssessment]:
        with self._lock:
            records = list(self._assessments.values())
        return _filter_assessments(records, workflow_id)

    def create_approval(self, approval: AssessmentApproval) -> AssessmentApproval:
        with self._lock:
            if approval.assessment_id in self._approvals:
                raise DependencyAssessmentAlreadyExistsError(
                    f"Assessment '{approval.assessment_id}' is already approved"
                )
            self._approvals[approval.assessment_id] = approval
        return approval

    def get_approval(self, assessment_id: str) -> AssessmentApproval:
        with self._lock:
            try:
                return self._approvals[assessment_id]
            except KeyError as err:
                raise DependencyAssessmentNotFoundError(
                    f"Approval for assessment '{assessment_id}' was not found"
                ) from err

    def latest_approval(self, workflow_id: str) -> AssessmentApproval:
        with self._lock:
            records = [
                item
                for item in self._approvals.values()
                if item.workflow_id == workflow_id
            ]
        if not records:
            raise DependencyAssessmentNotFoundError(
                f"No approved dependency assessment exists for workflow '{workflow_id}'"
            )
        return max(records, key=lambda item: (item.approved_at, item.assessment_id))


def _filter_assessments(
    records: list[DependencyAssessment], workflow_id: str | None
) -> list[DependencyAssessment]:
    selected = [
        item for item in records if workflow_id is None or item.workflow_id == workflow_id
    ]
    return sorted(
        selected,
        key=lambda item: (item.created_at, item.assessment_id),
        reverse=True,
    )


class SqlAlchemyDependencyAssessmentRepository:
    """Store Component 1 artifacts in the team's dialect-neutral JSON table."""

    _ASSESSMENT_PREFIX = "workflow_dependency_assessment:"
    _APPROVAL_PREFIX = "workflow_dependency_approval:"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def _assessment_type(cls, assessment_id: str) -> str:
        return f"{cls._ASSESSMENT_PREFIX}{assessment_id}"

    @classmethod
    def _approval_type(cls, assessment_id: str) -> str:
        return f"{cls._APPROVAL_PREFIX}{assessment_id}"

    def _create(self, artifact_type: str, payload: dict, duplicate_message: str) -> None:
        with Session(self._engine) as session, session.begin():
            existing = session.scalar(
                select(ResearchArtifact.id).where(
                    ResearchArtifact.artifact_type == artifact_type
                )
            )
            if existing is not None:
                raise DependencyAssessmentAlreadyExistsError(duplicate_message)
            session.add(ResearchArtifact(artifact_type=artifact_type, payload=payload))

    def create_assessment(
        self, assessment: DependencyAssessment
    ) -> DependencyAssessment:
        self._create(
            self._assessment_type(assessment.assessment_id),
            assessment.to_mapping(),
            f"Dependency assessment '{assessment.assessment_id}' already exists",
        )
        return assessment

    def get_assessment(self, assessment_id: str) -> DependencyAssessment:
        with Session(self._engine) as session:
            artifact = session.scalar(
                select(ResearchArtifact).where(
                    ResearchArtifact.artifact_type
                    == self._assessment_type(assessment_id)
                )
            )
        if artifact is None:
            raise DependencyAssessmentNotFoundError(
                f"Dependency assessment '{assessment_id}' was not found"
            )
        return DependencyAssessment.from_mapping(artifact.payload)

    def list_assessments(
        self, workflow_id: str | None = None
    ) -> list[DependencyAssessment]:
        statement = select(ResearchArtifact).where(
            ResearchArtifact.artifact_type.like(f"{self._ASSESSMENT_PREFIX}%")
        )
        with Session(self._engine) as session:
            records = [
                DependencyAssessment.from_mapping(item.payload)
                for item in session.scalars(statement)
            ]
        return _filter_assessments(records, workflow_id)

    def create_approval(self, approval: AssessmentApproval) -> AssessmentApproval:
        self._create(
            self._approval_type(approval.assessment_id),
            approval.to_mapping(),
            f"Assessment '{approval.assessment_id}' is already approved",
        )
        return approval

    def get_approval(self, assessment_id: str) -> AssessmentApproval:
        with Session(self._engine) as session:
            artifact = session.scalar(
                select(ResearchArtifact).where(
                    ResearchArtifact.artifact_type == self._approval_type(assessment_id)
                )
            )
        if artifact is None:
            raise DependencyAssessmentNotFoundError(
                f"Approval for assessment '{assessment_id}' was not found"
            )
        return AssessmentApproval.from_mapping(artifact.payload)

    def latest_approval(self, workflow_id: str) -> AssessmentApproval:
        statement = select(ResearchArtifact).where(
            ResearchArtifact.artifact_type.like(f"{self._APPROVAL_PREFIX}%")
        )
        with Session(self._engine) as session:
            records = [
                AssessmentApproval.from_mapping(item.payload)
                for item in session.scalars(statement)
            ]
        selected = [item for item in records if item.workflow_id == workflow_id]
        if not selected:
            raise DependencyAssessmentNotFoundError(
                f"No approved dependency assessment exists for workflow '{workflow_id}'"
            )
        return max(selected, key=lambda item: (item.approved_at, item.assessment_id))
