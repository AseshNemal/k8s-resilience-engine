"""Coverage-aware multiclass evaluation kept separate from prediction."""

from __future__ import annotations

from datetime import datetime, timezone

from app.engine.workflow_dependency_modeling.classification_evaluation_models import (
    EVALUATED_LABELS,
    ClassificationEvaluation,
    ClassificationMetrics,
    DependencyClassificationReference,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    DependencyAssessment,
    ValidatedDependencyType,
)


def _metrics(
    label: ValidatedDependencyType,
    actual: dict[str, ValidatedDependencyType],
    predicted: dict[str, ValidatedDependencyType],
) -> ClassificationMetrics:
    services = set(actual) & set(predicted)
    tp = sum(actual[name] is label and predicted[name] is label for name in services)
    fp = sum(actual[name] is not label and predicted[name] is label for name in services)
    fn = sum(actual[name] is label and predicted[name] is not label for name in services)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return ClassificationMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        support=sum(value is label for value in actual.values()),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
    )


def evaluate_dependency_classification(
    assessment: DependencyAssessment,
    reference: DependencyClassificationReference,
    *,
    evaluation_id: str,
    actor: str,
    now: datetime | None = None,
) -> ClassificationEvaluation:
    """Evaluate a completed assessment without exposing truth to its classifier."""
    if assessment.workflow_id != reference.workflow_id:
        raise ValueError("Assessment and reference workflow IDs do not match")
    if assessment.scenario_version != reference.scenario_version:
        raise ValueError("Assessment and reference scenario versions do not match")

    actual = {item.service_name: item.classification for item in reference.labels}
    observed = {item.service_name: item.classification for item in assessment.services}
    predicted = {
        name: label
        for name, label in observed.items()
        if name in actual and label in EVALUATED_LABELS
    }
    expected_names = set(actual)
    observed_names = set(observed)
    abstained = sorted(
        name
        for name in expected_names & observed_names
        if observed[name] is ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    )
    missing = sorted(expected_names - observed_names)
    unexpected = sorted(observed_names - expected_names)
    classified_names = expected_names & set(predicted)
    misclassified = sorted(
        name for name in classified_names if actual[name] is not predicted[name]
    )

    matrix = {
        actual_label.value: {predicted_label.value: 0 for predicted_label in EVALUATED_LABELS}
        for actual_label in EVALUATED_LABELS
    }
    for name in classified_names:
        matrix[actual[name].value][predicted[name].value] += 1

    per_class = {
        label.value: _metrics(label, actual, predicted) for label in EVALUATED_LABELS
    }
    correct = sum(actual[name] is predicted[name] for name in classified_names)
    classified_count = len(classified_names)
    expected_count = len(expected_names)
    total_tp = sum(item.true_positives for item in per_class.values())
    total_fp = sum(item.false_positives for item in per_class.values())
    total_fn = sum(item.false_negatives for item in per_class.values())
    micro_precision = total_tp / (total_tp + total_fp) if total_tp + total_fp else 0.0
    micro_recall = total_tp / (total_tp + total_fn) if total_tp + total_fn else 0.0
    micro_f1 = (
        2 * micro_precision * micro_recall / (micro_precision + micro_recall)
        if micro_precision + micro_recall
        else 0.0
    )
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("Evaluation timestamps must be timezone-aware")
    return ClassificationEvaluation(
        evaluation_id=evaluation_id,
        workflow_id=assessment.workflow_id,
        scenario_version=assessment.scenario_version,
        environment_id=assessment.environment_id,
        assessment_ids=(assessment.assessment_id,),
        reference_id=reference.reference_id,
        reference_version=reference.reference_version,
        synthetic=reference.synthetic,
        confusion_matrix=matrix,
        per_class=per_class,
        accuracy=correct / classified_count if classified_count else 0.0,
        macro_f1=sum(item.f1 for item in per_class.values()) / len(per_class),
        micro_f1=micro_f1,
        coverage=classified_count / expected_count if expected_count else 0.0,
        abstention_rate=(len(abstained) + len(missing)) / expected_count,
        misclassified_services=tuple(misclassified),
        abstained_services=tuple(abstained),
        missing_services=tuple(missing),
        unexpected_services=tuple(unexpected),
        created_at=timestamp.astimezone(timezone.utc),
        created_by=actor,
    )
