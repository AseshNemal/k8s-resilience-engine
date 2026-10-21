"""Create JSON-ready Component 3 validation evidence."""

from datetime import datetime, timezone

from app.engine.failure_impact_prediction.validation_metrics import (
    ClassificationMetrics,
)
from app.engine.failure_impact_prediction.validation_summary import (
    PredictionValidationSummary,
)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Validation timestamps must include timezone information")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _classification_export(
    metrics: ClassificationMetrics,
) -> dict[str, object]:
    return {
        "truePositives": metrics.counts.true_positives,
        "falsePositives": metrics.counts.false_positives,
        "falseNegatives": metrics.counts.false_negatives,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1Score": metrics.f1_score,
    }


def build_validation_summary_export(
    summary: PredictionValidationSummary,
) -> dict[str, object]:
    """Return a JSON-serializable evidence artifact without writing to disk."""

    batch = summary.batch
    metrics = summary.metrics
    if metrics.batch_id != batch.batch_id:
        raise ValueError("Validation batch and metrics must have the same batch_id")
    if metrics.total_experiments != batch.total_experiments:
        raise ValueError(
            "Validation batch and metrics must have the same experiment count"
        )

    experiments: list[dict[str, object]] = []
    for record in batch.records:
        comparison = record.comparison
        experiments.append(
            {
                "experimentId": record.experiment_id,
                "workflowId": record.workflow_id,
                "failedNode": record.failed_node,
                "observationSource": record.observation_source,
                "observedAt": _timestamp(record.observed_at),
                "workflowFreezeId": record.workflow_freeze_id,
                "deploymentSnapshotId": record.deployment_snapshot_id,
                "namespace": record.namespace,
                "predictedFailedServices": list(
                    comparison.predicted_failed_services
                ),
                "observedFailedServices": list(
                    comparison.observed_failed_services
                ),
                "serviceTruePositives": list(
                    comparison.service_true_positives
                ),
                "serviceFalsePositives": list(
                    comparison.service_false_positives
                ),
                "serviceFalseNegatives": list(
                    comparison.service_false_negatives
                ),
                "predictedFailedWorkflows": list(
                    comparison.predicted_failed_workflows
                ),
                "observedFailedWorkflows": list(
                    comparison.observed_failed_workflows
                ),
                "workflowTruePositives": list(
                    comparison.workflow_true_positives
                ),
                "workflowFalsePositives": list(
                    comparison.workflow_false_positives
                ),
                "workflowFalseNegatives": list(
                    comparison.workflow_false_negatives
                ),
                "servicesMatch": comparison.services_match,
                "workflowsMatch": comparison.workflows_match,
                "exactMatch": comparison.exact_match,
            }
        )

    return {
        "batchId": batch.batch_id,
        "totalExperiments": batch.total_experiments,
        "exactMatchExperimentIds": list(batch.exact_match_experiment_ids),
        "serviceMismatchExperimentIds": list(
            batch.service_mismatch_experiment_ids
        ),
        "workflowMismatchExperimentIds": list(
            batch.workflow_mismatch_experiment_ids
        ),
        "exactMatchCount": metrics.exact_match_count,
        "exactMatchRate": metrics.exact_match_rate,
        "serviceMetrics": _classification_export(metrics.service_metrics),
        "workflowMetrics": _classification_export(metrics.workflow_metrics),
        "experiments": experiments,
    }


__all__ = ["build_validation_summary_export"]
