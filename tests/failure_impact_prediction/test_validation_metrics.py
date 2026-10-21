from datetime import datetime, timezone

import pytest

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
    build_prediction_validation_record,
)
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.failure_impact_prediction.validation_batch import (
    PredictionValidationBatch,
    build_prediction_validation_batch,
)
from app.engine.failure_impact_prediction.validation_metrics import (
    ClassificationCounts,
    calculate_validation_batch_metrics,
)


def _record(
    experiment_id: str,
    *,
    failed_node: str = "node-1",
    failed_services: tuple[str, ...] = ("cartservice",),
    failed_workflows: tuple[str, ...] = ("checkout",),
):
    workflow, deployment = get_demo_inputs()
    prediction = predict_failure_impact(workflow, deployment, failed_node)
    observation = ObservedFailureOutcome(
        experiment_id=experiment_id,
        failed_node=failed_node,
        failed_services=failed_services,
        failed_workflows=failed_workflows,
    )
    return build_prediction_validation_record(
        prediction,
        observation,
        observation_source="controlled experiment",
        observed_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )


def test_metrics_micro_average_repeated_experiment_counts() -> None:
    exact = _record("experiment-exact")
    missed_service = _record(
        "experiment-missed-service",
        failed_services=("cartservice", "paymentservice"),
    )
    overpredicted_workflow = _record(
        "experiment-overpredicted-workflow",
        failed_workflows=(),
    )
    batch = build_prediction_validation_batch(
        "node-1-validation",
        [exact, missed_service, overpredicted_workflow],
    )

    metrics = calculate_validation_batch_metrics(batch)

    assert metrics.batch_id == "node-1-validation"
    assert metrics.total_experiments == 3
    assert metrics.exact_match_count == 1
    assert metrics.exact_match_rate == pytest.approx(1 / 3)
    assert metrics.service_metrics.counts == ClassificationCounts(
        true_positives=3,
        false_positives=0,
        false_negatives=1,
    )
    assert metrics.service_metrics.precision == pytest.approx(1.0)
    assert metrics.service_metrics.recall == pytest.approx(0.75)
    assert metrics.service_metrics.f1_score == pytest.approx(6 / 7)
    assert metrics.workflow_metrics.counts == ClassificationCounts(
        true_positives=2,
        false_positives=1,
        false_negatives=0,
    )
    assert metrics.workflow_metrics.precision == pytest.approx(2 / 3)
    assert metrics.workflow_metrics.recall == pytest.approx(1.0)
    assert metrics.workflow_metrics.f1_score == pytest.approx(0.8)


def test_metrics_return_none_when_no_failure_classification_exists() -> None:
    no_failure = _record(
        "experiment-no-failure",
        failed_node="node-2",
        failed_services=(),
        failed_workflows=(),
    )
    batch = build_prediction_validation_batch(
        "no-failure-validation",
        [no_failure],
    )

    metrics = calculate_validation_batch_metrics(batch)

    assert metrics.exact_match_rate == pytest.approx(1.0)
    assert metrics.service_metrics.counts == ClassificationCounts(0, 0, 0)
    assert metrics.service_metrics.precision is None
    assert metrics.service_metrics.recall is None
    assert metrics.service_metrics.f1_score is None
    assert metrics.workflow_metrics.counts == ClassificationCounts(0, 0, 0)
    assert metrics.workflow_metrics.precision is None
    assert metrics.workflow_metrics.recall is None
    assert metrics.workflow_metrics.f1_score is None


def test_metrics_keep_undefined_recall_when_only_false_positives_exist() -> None:
    overprediction = _record(
        "experiment-only-false-positives",
        failed_services=(),
        failed_workflows=(),
    )
    batch = build_prediction_validation_batch(
        "false-positive-validation",
        [overprediction],
    )

    metrics = calculate_validation_batch_metrics(batch)

    assert metrics.service_metrics.counts == ClassificationCounts(0, 1, 0)
    assert metrics.service_metrics.precision == pytest.approx(0.0)
    assert metrics.service_metrics.recall is None
    assert metrics.service_metrics.f1_score is None
    assert metrics.workflow_metrics.counts == ClassificationCounts(0, 1, 0)
    assert metrics.workflow_metrics.precision == pytest.approx(0.0)
    assert metrics.workflow_metrics.recall is None
    assert metrics.workflow_metrics.f1_score is None


def test_metrics_reject_manually_constructed_empty_batch() -> None:
    empty_batch = PredictionValidationBatch(
        batch_id="invalid-empty-batch",
        records=(),
        exact_match_experiment_ids=(),
        service_mismatch_experiment_ids=(),
        workflow_mismatch_experiment_ids=(),
    )

    with pytest.raises(ValueError, match="At least one validation record"):
        calculate_validation_batch_metrics(empty_batch)


def test_metrics_do_not_modify_validation_batch() -> None:
    record = _record("experiment-immutable")
    batch = build_prediction_validation_batch(
        "immutable-validation",
        [record],
    )

    calculate_validation_batch_metrics(batch)

    assert batch.records == (record,)
    assert batch.exact_match_experiment_ids == ("experiment-immutable",)
