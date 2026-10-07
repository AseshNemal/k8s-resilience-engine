"""Application service for governed classification research evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.classification_evaluation_models import (
    ClassificationEvaluation,
    DependencyClassificationReference,
)
from app.engine.workflow_dependency_modeling.classification_evaluation_repository import (
    ClassificationEvaluationRepository,
    SqlAlchemyClassificationEvaluationRepository,
)
from app.engine.workflow_dependency_modeling.classification_evaluator import (
    evaluate_dependency_classification,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    DependencyAssessmentRepository,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_service import (
    get_dependency_assessment,
)
from app.engine.workflow_dependency_modeling.scenario_service import (
    get_versioned_scenario,
)


_default_repository: ClassificationEvaluationRepository | None = None
_REFERENCE_SERVER_FIELDS = {
    "referenceId",
    "referenceVersion",
    "createdAt",
    "createdBy",
}
_EVALUATION_SERVER_FIELDS = {
    "confusionMatrix",
    "perClass",
    "accuracy",
    "macroF1",
    "microF1",
    "coverage",
    "abstentionRate",
    "createdAt",
    "createdBy",
}


def configure_classification_evaluation_repository(
    repository: ClassificationEvaluationRepository,
) -> None:
    global _default_repository
    _default_repository = repository


def _repository(
    repository: ClassificationEvaluationRepository | None,
) -> ClassificationEvaluationRepository:
    if repository is not None:
        return repository
    global _default_repository
    if _default_repository is None:
        from app.core.config import get_settings
        from app.core.database import get_engine

        _default_repository = SqlAlchemyClassificationEvaluationRepository(
            get_engine(get_settings().database_url)
        )
    return _default_repository


def _timestamp(value: datetime | None = None) -> str:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None:
        raise ValueError("Classification artifact timestamps must be timezone-aware")
    return resolved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def create_dependency_classification_reference(
    data: Mapping[str, Any],
    *,
    actor: str,
    expected_version: int,
    repository: ClassificationEvaluationRepository | None = None,
    now: datetime | None = None,
) -> DependencyClassificationReference:
    forbidden = _REFERENCE_SERVER_FIELDS & data.keys()
    if forbidden:
        raise ValueError(
            f"Server-managed fields are not accepted: {', '.join(sorted(forbidden))}"
        )
    if expected_version < 0:
        raise ValueError("expected_version cannot be negative")
    workflow_id = str(data.get("workflowId", "")).strip()
    scenario_version = data.get("scenarioVersion")
    get_versioned_scenario(workflow_id, scenario_version)
    version = expected_version + 1
    reference = DependencyClassificationReference.from_mapping(
        {
            **data,
            "referenceId": f"{workflow_id}:classification-reference:v{version}",
            "referenceVersion": version,
            "createdAt": _timestamp(now),
            "createdBy": actor,
        }
    )
    return _repository(repository).add_reference(reference, expected_version)


def get_dependency_classification_reference(
    workflow_id: str,
    version: int | None = None,
    repository: ClassificationEvaluationRepository | None = None,
) -> DependencyClassificationReference:
    return _repository(repository).get_reference(workflow_id, version)


def list_dependency_classification_references(
    repository: ClassificationEvaluationRepository | None = None,
) -> list[DependencyClassificationReference]:
    return _repository(repository).list_references()


def create_dependency_classification_evaluation(
    data: Mapping[str, Any],
    *,
    actor: str,
    repository: ClassificationEvaluationRepository | None = None,
    assessment_repository: DependencyAssessmentRepository | None = None,
    now: datetime | None = None,
) -> ClassificationEvaluation:
    forbidden = _EVALUATION_SERVER_FIELDS & data.keys()
    if forbidden:
        raise ValueError(
            f"Server-managed fields are not accepted: {', '.join(sorted(forbidden))}"
        )
    evaluation_id = str(data.get("evaluationId", "")).strip()
    assessment_id = str(data.get("assessmentId", "")).strip()
    if not evaluation_id or not assessment_id:
        raise ValueError("evaluationId and assessmentId are required")

    # The assessment is loaded first. Reference truth is only accessed after
    # prediction/classification has already been frozen and persisted.
    assessment = get_dependency_assessment(
        assessment_id, repository=assessment_repository
    )
    reference_version = data.get("referenceVersion")
    reference = _repository(repository).get_reference(
        assessment.workflow_id, reference_version
    )
    evaluation = evaluate_dependency_classification(
        assessment,
        reference,
        evaluation_id=evaluation_id,
        actor=actor,
        now=now,
    )
    return _repository(repository).create_evaluation(evaluation)


def get_dependency_classification_evaluation(
    evaluation_id: str,
    repository: ClassificationEvaluationRepository | None = None,
) -> ClassificationEvaluation:
    return _repository(repository).get_evaluation(evaluation_id)


def list_dependency_classification_evaluations(
    workflow_id: str | None = None,
    repository: ClassificationEvaluationRepository | None = None,
) -> list[ClassificationEvaluation]:
    return _repository(repository).list_evaluations(workflow_id)
