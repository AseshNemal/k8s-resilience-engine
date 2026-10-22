"""Export aggregate metrics for repeated method-evaluation experiments."""

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from app.engine.failure_impact_prediction.method_evaluation_batch import (
    AggregatedFailureSetMetrics,
    AggregatedMethodMetrics,
    FailureImpactMethodEvaluationBatch,
)


ARTIFACT_TYPE = "FAILURE_IMPACT_METHOD_EVALUATION_BATCH"


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("generated_at must include timezone information")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _dimension_export(
    metrics: AggregatedFailureSetMetrics | None,
) -> object:
    if metrics is None:
        return None
    classification = metrics.classification
    return {
        "evaluatedExperiments": metrics.evaluated_experiments,
        "exactMatchCount": metrics.exact_match_count,
        "exactMatchRate": metrics.exact_match_rate,
        "truePositives": classification.counts.true_positives,
        "falsePositives": classification.counts.false_positives,
        "falseNegatives": classification.counts.false_negatives,
        "precision": classification.precision,
        "recall": classification.recall,
        "f1Score": classification.f1_score,
    }


def _method_export(metrics: AggregatedMethodMetrics) -> dict[str, object]:
    return {
        "method": metrics.method,
        "interpretation": metrics.interpretation,
        "serviceMetrics": _dimension_export(metrics.service_metrics),
        "workflowMetrics": _dimension_export(metrics.workflow_metrics),
    }


def build_method_evaluation_batch_export(
    batch: FailureImpactMethodEvaluationBatch,
    *,
    generated_at: datetime,
) -> dict[str, object]:
    """Return a JSON-ready aggregate report for repeated experiments."""

    return {
        "artifactType": ARTIFACT_TYPE,
        "generatedAt": _timestamp(generated_at),
        "batchId": batch.batch_id,
        "totalExperiments": batch.total_experiments,
        "experimentIds": list(batch.experiment_ids),
        "methods": {
            "replicaCountOnly": _method_export(batch.replica_count_only),
            "deploymentOnly": _method_export(batch.deployment_only),
            "dependencyOnly": _method_export(batch.dependency_only),
            "proposedCrossLayer": _method_export(
                batch.proposed_cross_layer
            ),
        },
    }


def write_method_evaluation_batch_artifact(
    path: Path,
    artifact: Mapping[str, object],
) -> Path:
    """Write a new aggregate artifact without replacing earlier results."""

    if artifact.get("artifactType") != ARTIFACT_TYPE:
        raise ValueError(
            f"batch artifact must have artifactType {ARTIFACT_TYPE}"
        )

    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            json.dump(artifact, stream, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise ValueError(
            f"refusing to overwrite existing method batch artifact: {path}"
        ) from error
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    return path


__all__ = [
    "ARTIFACT_TYPE",
    "build_method_evaluation_batch_export",
    "write_method_evaluation_batch_artifact",
]
