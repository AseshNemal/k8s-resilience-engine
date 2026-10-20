from datetime import datetime, timezone

import pytest

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
)
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.failure_impact_prediction.validation_metrics import (
    ClassificationCounts,
)
from app.engine.failure_impact_prediction.validation_pipeline import (
    PredictionValidationExperiment,
    evaluate_prediction_experiments,
)


def _experiment(
    experiment_id: str,
    *,
    observed_failed_node: str = "node-1",
    failed_services: tuple[str, ...] = ("cartservice",),
    failed_workflows: tuple[str, ...] = ("checkout",),
) -> PredictionValidationExperiment:
    workflow, deployment = get_demo_inputs()
    prediction = predict_failure_impact(workflow, deployment, "node-1")
    observation = ObservedFailureOutcome(
        experiment_id=experiment_id,
        failed_node=observed_failed_node,
        failed_services=failed_services,
        failed_workflows=failed_workflows,
    )
    return PredictionValidationExperiment(
        prediction=prediction,
        observation=observation,
        observation_source="controlled node shutdown",
        observed_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        workflow_freeze_id="checkout-freeze-v1",
        deployment_snapshot_id="deployment-before-node-1",
        namespace="research",
    )


def test_pipeline_builds_traceable_summary_and_metrics() -> None:
    exact = _experiment("experiment-exact")
    missed_service = _experiment(
        "experiment-missed-service",
        failed_services=("cartservice", "paymentservice"),
    )

    summary = evaluate_prediction_experiments(
        "node-1-validation",
        [exact, missed_service],
    )

    assert summary.batch.total_experiments == 2
    assert summary.batch.records[0].workflow_freeze_id == "checkout-freeze-v1"
    assert summary.batch.records[0].deployment_snapshot_id == (
        "deployment-before-node-1"
    )
    assert summary.batch.records[0].namespace == "research"
    assert summary.metrics.exact_match_count == 1
    assert summary.metrics.exact_match_rate == pytest.approx(0.5)
    assert summary.metrics.service_metrics.counts == ClassificationCounts(
        true_positives=2,
        false_positives=0,
        false_negatives=1,
    )
    assert summary.metrics.service_metrics.precision == pytest.approx(1.0)
    assert summary.metrics.service_metrics.recall == pytest.approx(2 / 3)
    assert summary.metrics.service_metrics.f1_score == pytest.approx(0.8)


def test_pipeline_rejects_empty_experiment_collection() -> None:
    with pytest.raises(ValueError, match="validation experiment"):
        evaluate_prediction_experiments("empty-batch", [])


def test_pipeline_propagates_prediction_observation_node_mismatch() -> None:
    mismatch = _experiment(
        "experiment-wrong-node",
        observed_failed_node="node-2",
        failed_services=(),
        failed_workflows=(),
    )

    with pytest.raises(ValueError, match="same failed node"):
        evaluate_prediction_experiments("invalid-batch", [mismatch])


def test_pipeline_does_not_retain_mutable_experiment_sequence() -> None:
    experiment = _experiment("experiment-immutable")
    source_experiments = [experiment]

    summary = evaluate_prediction_experiments(
        "immutable-batch",
        source_experiments,
    )
    source_experiments.append(_experiment("experiment-later"))

    assert summary.batch.total_experiments == 1
    assert summary.batch.records[0].experiment_id == "experiment-immutable"
