"""Orchestrate Component 3 prediction-validation experiments."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
    build_prediction_validation_record,
)
from app.engine.failure_impact_prediction.validation_summary import (
    PredictionValidationSummary,
    build_prediction_validation_summary,
)
from app.shared.schemas.failure_impact import FailureImpactReport


@dataclass(frozen=True)
class PredictionValidationExperiment:
    """Prediction, observed outcome, and provenance for one experiment."""

    prediction: FailureImpactReport
    observation: ObservedFailureOutcome
    observation_source: str
    observed_at: datetime
    workflow_freeze_id: str | None = None
    deployment_snapshot_id: str | None = None
    namespace: str | None = None


def evaluate_prediction_experiments(
    batch_id: str,
    experiments: Sequence[PredictionValidationExperiment],
) -> PredictionValidationSummary:
    """Compare, group, and measure repeated experiments in one operation."""

    if not experiments:
        raise ValueError("At least one validation experiment is required")

    records = tuple(
        build_prediction_validation_record(
            experiment.prediction,
            experiment.observation,
            observation_source=experiment.observation_source,
            observed_at=experiment.observed_at,
            workflow_freeze_id=experiment.workflow_freeze_id,
            deployment_snapshot_id=experiment.deployment_snapshot_id,
            namespace=experiment.namespace,
        )
        for experiment in experiments
    )
    return build_prediction_validation_summary(batch_id, records)


__all__ = [
    "PredictionValidationExperiment",
    "evaluate_prediction_experiments",
]
