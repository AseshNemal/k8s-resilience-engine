"""Export evidence-backed comparisons between failure-impact methods."""

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from app.engine.failure_impact_prediction.method_evaluation import (
    FailureImpactMethodEvaluation,
    FailureSetEvaluation,
    MethodEvidenceEvaluation,
)
from app.engine.failure_impact_prediction.validation_evidence import (
    ObservedFailureEvidence,
)


ARTIFACT_TYPE = "FAILURE_IMPACT_METHOD_EVALUATION"


def _timestamp(value: datetime, field_name: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _set_export(evaluation: FailureSetEvaluation | None) -> object:
    if evaluation is None:
        return None
    metrics = evaluation.metrics
    return {
        "observationScope": list(evaluation.observation_scope),
        "predictedFailures": list(evaluation.predicted_failures),
        "observedFailures": list(evaluation.observed_failures),
        "truePositives": list(evaluation.true_positives),
        "falsePositives": list(evaluation.false_positives),
        "falseNegatives": list(evaluation.false_negatives),
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1Score": metrics.f1_score,
        "exactMatch": evaluation.exact_match,
    }


def _method_export(evaluation: MethodEvidenceEvaluation) -> dict[str, object]:
    return {
        "method": evaluation.method,
        "interpretation": evaluation.interpretation,
        "serviceEvaluation": _set_export(evaluation.service_evaluation),
        "workflowEvaluation": _set_export(evaluation.workflow_evaluation),
    }


def build_method_evaluation_export(
    evaluation: FailureImpactMethodEvaluation,
    evidence: ObservedFailureEvidence,
    *,
    comparison_generated_at: datetime,
    evaluated_at: datetime,
) -> dict[str, object]:
    """Build a JSON-ready evaluation with prediction and evidence provenance."""

    if evaluation.experiment_id != evidence.experiment_id:
        raise ValueError("Evaluation and evidence must use the same experiment ID")
    if evaluation.failed_node != evidence.failed_node:
        raise ValueError("Evaluation and evidence must use the same failed node")
    comparison_timestamp = _timestamp(
        comparison_generated_at,
        "comparison_generated_at",
    )
    observation_timestamp = _timestamp(evidence.observed_at, "observed_at")
    evaluation_timestamp = _timestamp(evaluated_at, "evaluated_at")
    if evaluated_at < evidence.observed_at:
        raise ValueError("evaluated_at cannot be earlier than observed_at")

    return {
        "artifactType": ARTIFACT_TYPE,
        "experimentId": evaluation.experiment_id,
        "failedNode": evaluation.failed_node,
        "comparisonGeneratedAt": comparison_timestamp,
        "observedAt": observation_timestamp,
        "evaluatedAt": evaluation_timestamp,
        "observationSource": evidence.observation_source,
        "workflowFreezeId": evidence.workflow_freeze_id,
        "deploymentSnapshotId": evidence.deployment_snapshot_id,
        "namespace": evidence.namespace,
        "methods": {
            "replicaCountOnly": _method_export(
                evaluation.replica_count_only
            ),
            "deploymentOnly": _method_export(evaluation.deployment_only),
            "dependencyOnly": _method_export(evaluation.dependency_only),
            "proposedCrossLayer": _method_export(
                evaluation.proposed_cross_layer
            ),
        },
    }


def write_method_evaluation_artifact(
    path: Path,
    artifact: Mapping[str, object],
) -> Path:
    """Write one new method-evaluation artifact without overwriting."""

    if artifact.get("artifactType") != ARTIFACT_TYPE:
        raise ValueError(
            f"method evaluation artifact must have artifactType {ARTIFACT_TYPE}"
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
            f"refusing to overwrite existing method evaluation artifact: {path}"
        ) from error
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    return path


__all__ = [
    "ARTIFACT_TYPE",
    "build_method_evaluation_export",
    "write_method_evaluation_artifact",
]
