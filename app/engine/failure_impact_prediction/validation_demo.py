"""Synthetic Component 3 validation demonstration for development and viva use."""

from datetime import datetime, timedelta, timezone

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
)
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.failure_impact_prediction.validation_export import (
    build_validation_summary_export,
)
from app.engine.failure_impact_prediction.validation_pipeline import (
    PredictionValidationExperiment,
    evaluate_prediction_experiments,
)


SYNTHETIC_EVIDENCE_STATUS = "SYNTHETIC_DEMO"
SYNTHETIC_OBSERVATION_SOURCE = (
    "synthetic assumed outcome for demonstration; not controlled evidence"
)
SYNTHETIC_EVIDENCE_DISCLAIMER = (
    "These assumed outcomes demonstrate the validation pipeline only. "
    "They are not results from a Kubernetes failure experiment."
)


def build_synthetic_validation_demo() -> dict[str, object]:
    """Build a deterministic demo without claiming empirical validation."""

    workflow, deployment = get_demo_inputs()
    prediction = predict_failure_impact(workflow, deployment, "node-1")
    observed_at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    assumed_outcomes = (
        ObservedFailureOutcome(
            experiment_id="synthetic-exact",
            failed_node="node-1",
            failed_services=("cartservice",),
            failed_workflows=("checkout",),
        ),
        ObservedFailureOutcome(
            experiment_id="synthetic-missed-service",
            failed_node="node-1",
            failed_services=("cartservice", "paymentservice"),
            failed_workflows=("checkout",),
        ),
        ObservedFailureOutcome(
            experiment_id="synthetic-overpredicted-workflow",
            failed_node="node-1",
            failed_services=("cartservice",),
            failed_workflows=(),
        ),
    )
    experiments = tuple(
        PredictionValidationExperiment(
            prediction=prediction,
            observation=outcome,
            observation_source=SYNTHETIC_OBSERVATION_SOURCE,
            observed_at=observed_at + timedelta(minutes=index),
            workflow_freeze_id="demo-workflow-fixture",
            deployment_snapshot_id="demo-deployment-fixture",
            namespace="synthetic-demo",
        )
        for index, outcome in enumerate(assumed_outcomes)
    )
    summary = evaluate_prediction_experiments(
        "synthetic-node-1-validation",
        experiments,
    )

    return {
        "evidenceStatus": SYNTHETIC_EVIDENCE_STATUS,
        "disclaimer": SYNTHETIC_EVIDENCE_DISCLAIMER,
        "validation": build_validation_summary_export(summary),
    }


__all__ = [
    "SYNTHETIC_EVIDENCE_DISCLAIMER",
    "SYNTHETIC_EVIDENCE_STATUS",
    "SYNTHETIC_OBSERVATION_SOURCE",
    "build_synthetic_validation_demo",
]
