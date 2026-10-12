"""Approval-gated graph freeze with reproducible evidence provenance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    DependencyAssessmentRepository,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentApproval,
    DependencyAssessment,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_service import (
    dependency_assessment_repository,
)
from app.engine.workflow_dependency_modeling.execution_models import (
    CorrelationStatus,
    ExecutionOutcome,
    ExecutionOutcomeStatus,
)
from app.engine.workflow_dependency_modeling.execution_repository import (
    ExecutionNotFoundError,
    ExecutionRepository,
)
from app.engine.workflow_dependency_modeling.execution_service import list_executions
from app.engine.workflow_dependency_modeling.graph_freeze_models import FrozenWorkflowGraph
from app.engine.workflow_dependency_modeling.graph_freeze_repository import (
    GraphFreezeRepository,
    SqlAlchemyGraphFreezeRepository,
)


ALGORITHM_VERSION = "workflow-dependency-modeling/phase-5-validation-2"
_default_repository: GraphFreezeRepository | None = None


def configure_graph_freeze_repository(repository: GraphFreezeRepository) -> None:
    global _default_repository
    _default_repository = repository


def _repository(repository: GraphFreezeRepository | None) -> GraphFreezeRepository:
    if repository is not None:
        return repository
    global _default_repository
    if _default_repository is None:
        from app.core.config import get_settings
        from app.core.database import get_engine

        _default_repository = SqlAlchemyGraphFreezeRepository(
            get_engine(get_settings().database_url)
        )
    return _default_repository


def _freeze_identity(data: Mapping[str, Any], actor: str) -> tuple[str, str, str]:
    unknown_fields = sorted(set(data) - {"assessmentId", "freezeId"})
    if unknown_fields:
        raise ValueError(
            "Freeze provenance is server-derived; unsupported fields: "
            + ", ".join(unknown_fields)
        )
    assessment_id = str(data.get("assessmentId", "")).strip()
    freeze_id = str(data.get("freezeId", "")).strip()
    if not assessment_id or not freeze_id:
        raise ValueError("freezeId and assessmentId are required")
    frozen_by = actor.strip() if isinstance(actor, str) else ""
    if not frozen_by:
        raise ValueError("actor is required")
    return assessment_id, freeze_id, frozen_by


def _approved_assessment(
    workflow_id: str,
    assessment_id: str,
    repository: DependencyAssessmentRepository,
) -> tuple[AssessmentApproval, DependencyAssessment]:
    approval = repository.get_approval(assessment_id)
    assessment = repository.get_assessment(assessment_id)
    if approval.workflow_id != workflow_id or assessment.workflow_id != workflow_id:
        raise ValueError("Approved assessment does not belong to the requested workflow")
    if approval.assessment_id != assessment.assessment_id:
        raise ValueError("Approval does not belong to the stored assessment")
    if approval.scenario_version != assessment.scenario_version:
        raise ValueError("Approval scenario version does not match the assessment")
    if approval.approved_graph.workflow_id != workflow_id:
        raise ValueError("Approved graph workflow ID does not match the requested workflow")
    return approval, assessment


def _assessment_batch_ids(assessment: DependencyAssessment) -> set[str]:
    return {
        *assessment.baseline_batch_ids,
        *(
            batch_id
            for cohort in assessment.interventions
            for batch_id in cohort.batch_ids
        ),
        *(
            batch_id
            for cohort in assessment.edge_interventions
            for batch_id in cohort.batch_ids
        ),
    }


def _evidence_outcomes(
    batch_ids: set[str], repository: ExecutionRepository | None
) -> list[ExecutionOutcome]:
    outcomes: list[ExecutionOutcome] = []
    for batch_id in sorted(batch_ids):
        batch = list_executions(batch_id=batch_id, repository=repository)
        if not batch:
            raise ExecutionNotFoundError(
                f"Execution batch '{batch_id}' was not found while freezing provenance"
            )
        outcomes.extend(batch)
    return outcomes


def _trace_provenance(
    outcomes: list[ExecutionOutcome],
) -> tuple[tuple[str, ...], int]:
    matched_trace_ids = [
        outcome.matched_trace_id
        for outcome in outcomes
        if outcome.correlation_status is CorrelationStatus.MATCHED
        and outcome.matched_trace_id
    ]
    if len(matched_trace_ids) != len(set(matched_trace_ids)):
        raise ValueError(
            "An approved graph cannot be frozen when multiple executions reference "
            "the same matched trace"
        )
    eligible_trace_ids = [
        outcome.matched_trace_id
        for outcome in outcomes
        if outcome.correlation_status is CorrelationStatus.MATCHED
        and outcome.status
        in {ExecutionOutcomeStatus.SUCCEEDED, ExecutionOutcomeStatus.FAILED}
        and outcome.matched_trace_id
    ]
    valid_trace_ids = tuple(sorted(eligible_trace_ids))
    if not valid_trace_ids:
        raise ValueError(
            "An approved graph cannot be frozen without eligible correlated trace evidence"
        )
    excluded_count = len(outcomes) - len(valid_trace_ids)
    return valid_trace_ids, excluded_count


def freeze_workflow_graph(
    workflow_id: str,
    data: Mapping[str, Any],
    *,
    actor: str,
    repository: GraphFreezeRepository | None = None,
    dependency_repository: DependencyAssessmentRepository | None = None,
    execution_repository: ExecutionRepository | None = None,
    now: datetime | None = None,
) -> FrozenWorkflowGraph:
    """Freeze only an approved graph and derive provenance from its cohorts."""
    assessment_id, freeze_id, frozen_by = _freeze_identity(data, actor)
    assessment_repo = dependency_assessment_repository(dependency_repository)
    approval, assessment = _approved_assessment(
        workflow_id, assessment_id, assessment_repo
    )
    valid_trace_ids, excluded_count = _trace_provenance(
        _evidence_outcomes(_assessment_batch_ids(assessment), execution_repository)
    )
    frozen_at = now or datetime.now(timezone.utc)
    if frozen_at.tzinfo is None:
        raise ValueError("Freeze timestamp must be timezone-aware")
    artifact = FrozenWorkflowGraph(
        freeze_id=freeze_id,
        workflow_id=workflow_id,
        assessment_id=assessment_id,
        scenario_version=assessment.scenario_version,
        graph=approval.approved_graph,
        trace_ids=valid_trace_ids,
        valid_trace_count=len(valid_trace_ids),
        excluded_trace_count=excluded_count,
        algorithm_version=ALGORITHM_VERSION,
        mandatory_threshold=assessment.policy.mandatory_effect_threshold,
        frozen_at=frozen_at.astimezone(timezone.utc),
        frozen_by=frozen_by,
    )
    return _repository(repository).create(artifact)


def get_frozen_workflow_graph(
    workflow_id: str,
    repository: GraphFreezeRepository | None = None,
) -> FrozenWorkflowGraph:
    return _repository(repository).latest(workflow_id)
