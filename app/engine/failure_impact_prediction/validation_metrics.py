"""Approved aggregate metrics for Component 3 validation batches."""

from dataclasses import dataclass

from app.engine.failure_impact_prediction.validation_batch import (
    PredictionValidationBatch,
)


@dataclass(frozen=True)
class ClassificationCounts:
    """Micro-aggregated failure-classification counts."""

    true_positives: int
    false_positives: int
    false_negatives: int


@dataclass(frozen=True)
class ClassificationMetrics:
    """Precision, recall and F1 with explicit undefined values."""

    counts: ClassificationCounts
    precision: float | None
    recall: float | None
    f1_score: float | None


@dataclass(frozen=True)
class ValidationBatchMetrics:
    """Approved metrics calculated across repeated experiments."""

    batch_id: str
    total_experiments: int
    exact_match_count: int
    exact_match_rate: float
    service_metrics: ClassificationMetrics
    workflow_metrics: ClassificationMetrics


def calculate_classification_metrics(
    counts: ClassificationCounts,
) -> ClassificationMetrics:
    """Calculate precision, recall and F1 from explicit classification counts."""

    if min(
        counts.true_positives,
        counts.false_positives,
        counts.false_negatives,
    ) < 0:
        raise ValueError("Classification counts cannot be negative")

    precision_denominator = counts.true_positives + counts.false_positives
    recall_denominator = counts.true_positives + counts.false_negatives

    precision = (
        counts.true_positives / precision_denominator
        if precision_denominator
        else None
    )
    recall = (
        counts.true_positives / recall_denominator
        if recall_denominator
        else None
    )
    f1_score = (
        2 * precision * recall / (precision + recall)
        if precision is not None
        and recall is not None
        and precision + recall > 0
        else None
    )
    return ClassificationMetrics(
        counts=counts,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
    )


def calculate_validation_batch_metrics(
    batch: PredictionValidationBatch,
) -> ValidationBatchMetrics:
    """Calculate approved micro-averaged metrics for one validation batch."""

    if not batch.records:
        raise ValueError("At least one validation record is required")

    service_counts = ClassificationCounts(
        true_positives=sum(
            len(record.comparison.service_true_positives)
            for record in batch.records
        ),
        false_positives=sum(
            len(record.comparison.service_false_positives)
            for record in batch.records
        ),
        false_negatives=sum(
            len(record.comparison.service_false_negatives)
            for record in batch.records
        ),
    )
    workflow_counts = ClassificationCounts(
        true_positives=sum(
            len(record.comparison.workflow_true_positives)
            for record in batch.records
        ),
        false_positives=sum(
            len(record.comparison.workflow_false_positives)
            for record in batch.records
        ),
        false_negatives=sum(
            len(record.comparison.workflow_false_negatives)
            for record in batch.records
        ),
    )
    exact_match_count = len(batch.exact_match_experiment_ids)

    return ValidationBatchMetrics(
        batch_id=batch.batch_id,
        total_experiments=batch.total_experiments,
        exact_match_count=exact_match_count,
        exact_match_rate=exact_match_count / batch.total_experiments,
        service_metrics=calculate_classification_metrics(service_counts),
        workflow_metrics=calculate_classification_metrics(workflow_counts),
    )


__all__ = [
    "ClassificationCounts",
    "ClassificationMetrics",
    "ValidationBatchMetrics",
    "calculate_classification_metrics",
    "calculate_validation_batch_metrics",
]
