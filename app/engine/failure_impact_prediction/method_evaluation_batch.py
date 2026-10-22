"""Aggregate repeated failure-impact method evaluations."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.engine.failure_impact_prediction.method_evaluation import (
    FailureImpactMethodEvaluation,
    FailureSetEvaluation,
    MethodEvidenceEvaluation,
    MethodName,
)
from app.engine.failure_impact_prediction.validation_metrics import (
    ClassificationCounts,
    ClassificationMetrics,
    calculate_classification_metrics,
)


@dataclass(frozen=True)
class AggregatedFailureSetMetrics:
    """Micro-averaged metrics for one applicable prediction dimension."""

    evaluated_experiments: int
    exact_match_count: int
    exact_match_rate: float
    classification: ClassificationMetrics


@dataclass(frozen=True)
class AggregatedMethodMetrics:
    """Applicable aggregate dimensions for one research method."""

    method: MethodName
    service_metrics: AggregatedFailureSetMetrics | None
    workflow_metrics: AggregatedFailureSetMetrics | None
    interpretation: str


@dataclass(frozen=True)
class FailureImpactMethodEvaluationBatch:
    """Aggregate comparison of all methods across independent experiments."""

    batch_id: str
    total_experiments: int
    experiment_ids: tuple[str, ...]
    replica_count_only: AggregatedMethodMetrics
    deployment_only: AggregatedMethodMetrics
    dependency_only: AggregatedMethodMetrics
    proposed_cross_layer: AggregatedMethodMetrics


def _aggregate_dimension(
    values: Sequence[FailureSetEvaluation | None],
    field_name: str,
) -> AggregatedFailureSetMetrics | None:
    applicable = tuple(value for value in values if value is not None)
    if not applicable:
        return None
    if len(applicable) != len(values):
        raise ValueError(
            f"{field_name} applicability must be consistent across experiments"
        )

    counts = ClassificationCounts(
        true_positives=sum(len(value.true_positives) for value in applicable),
        false_positives=sum(len(value.false_positives) for value in applicable),
        false_negatives=sum(len(value.false_negatives) for value in applicable),
    )
    exact_match_count = sum(value.exact_match for value in applicable)
    return AggregatedFailureSetMetrics(
        evaluated_experiments=len(applicable),
        exact_match_count=exact_match_count,
        exact_match_rate=exact_match_count / len(applicable),
        classification=calculate_classification_metrics(counts),
    )


def _aggregate_method(
    values: Sequence[MethodEvidenceEvaluation],
    *,
    expected_method: MethodName,
    field_name: str,
) -> AggregatedMethodMetrics:
    if any(value.method != expected_method for value in values):
        raise ValueError(
            f"{field_name} must contain only {expected_method} evaluations"
        )
    interpretations = {value.interpretation for value in values}
    if len(interpretations) != 1:
        raise ValueError(
            f"{field_name} interpretation must be consistent across experiments"
        )

    return AggregatedMethodMetrics(
        method=expected_method,
        service_metrics=_aggregate_dimension(
            [value.service_evaluation for value in values],
            f"{field_name}.service_evaluation",
        ),
        workflow_metrics=_aggregate_dimension(
            [value.workflow_evaluation for value in values],
            f"{field_name}.workflow_evaluation",
        ),
        interpretation=values[0].interpretation,
    )


def build_method_evaluation_batch(
    batch_id: str,
    evaluations: Sequence[FailureImpactMethodEvaluation],
) -> FailureImpactMethodEvaluationBatch:
    """Micro-average repeated experiments without inventing N/A metrics."""

    normalized_batch_id = batch_id.strip()
    if not normalized_batch_id:
        raise ValueError("batch_id must be a non-empty string")
    if not evaluations:
        raise ValueError("At least one method evaluation is required")

    experiment_ids = tuple(
        evaluation.experiment_id.strip() for evaluation in evaluations
    )
    if any(not experiment_id for experiment_id in experiment_ids):
        raise ValueError("experiment IDs must be non-empty strings")
    duplicates = tuple(
        sorted(
            {
                experiment_id
                for experiment_id in experiment_ids
                if experiment_ids.count(experiment_id) > 1
            }
        )
    )
    if duplicates:
        raise ValueError(
            "Duplicate experiment IDs: " + ", ".join(duplicates)
        )

    return FailureImpactMethodEvaluationBatch(
        batch_id=normalized_batch_id,
        total_experiments=len(evaluations),
        experiment_ids=experiment_ids,
        replica_count_only=_aggregate_method(
            [evaluation.replica_count_only for evaluation in evaluations],
            expected_method="REPLICA_COUNT_ONLY",
            field_name="replica_count_only",
        ),
        deployment_only=_aggregate_method(
            [evaluation.deployment_only for evaluation in evaluations],
            expected_method="DEPLOYMENT_ONLY",
            field_name="deployment_only",
        ),
        dependency_only=_aggregate_method(
            [evaluation.dependency_only for evaluation in evaluations],
            expected_method="DEPENDENCY_ONLY",
            field_name="dependency_only",
        ),
        proposed_cross_layer=_aggregate_method(
            [evaluation.proposed_cross_layer for evaluation in evaluations],
            expected_method="PROPOSED_CROSS_LAYER",
            field_name="proposed_cross_layer",
        ),
    )


__all__ = [
    "AggregatedFailureSetMetrics",
    "AggregatedMethodMetrics",
    "FailureImpactMethodEvaluationBatch",
    "build_method_evaluation_batch",
]
