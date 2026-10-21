"""Orchestrate Component 3 prediction-validation experiments."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
    build_prediction_validation_record,
)
from app.engine.failure_impact_prediction.validation_evidence import (
    ObservedFailureEvidence,
    assess_validation_evidence,
    require_complete_observed_outcome,
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


@dataclass(frozen=True)
class PredictionEvidenceExperiment:
    """One prediction paired with its captured real-world evidence."""

    prediction: FailureImpactReport
    evidence: ObservedFailureEvidence


def prepare_prediction_validation_experiment(
    experiment: PredictionEvidenceExperiment,
) -> PredictionValidationExperiment:
    """Convert complete evidence while preserving its normalized provenance."""

    assessment = assess_validation_evidence(experiment.evidence)
    evidence = assessment.evidence
    observation = require_complete_observed_outcome(evidence)
    return PredictionValidationExperiment(
        prediction=experiment.prediction,
        observation=observation,
        observation_source=evidence.observation_source,
        observed_at=evidence.observed_at,
        workflow_freeze_id=evidence.workflow_freeze_id,
        deployment_snapshot_id=evidence.deployment_snapshot_id,
        namespace=evidence.namespace,
    )


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


def evaluate_prediction_evidence(
    batch_id: str,
    experiments: Sequence[PredictionEvidenceExperiment],
) -> PredictionValidationSummary:
    """Evaluate prediction/evidence pairs through the existing metric pipeline."""

    if not experiments:
        raise ValueError("At least one prediction evidence experiment is required")
    prepared = tuple(
        prepare_prediction_validation_experiment(experiment)
        for experiment in experiments
    )
    return evaluate_prediction_experiments(batch_id, prepared)


__all__ = [
    "PredictionEvidenceExperiment",
    "PredictionValidationExperiment",
    "evaluate_prediction_evidence",
    "evaluate_prediction_experiments",
    "prepare_prediction_validation_experiment",
]
