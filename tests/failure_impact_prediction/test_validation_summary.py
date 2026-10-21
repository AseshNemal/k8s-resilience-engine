from datetime import datetime, timezone

import pytest

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
    build_prediction_validation_record,
)
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.failure_impact_prediction.validation_metrics import (
    ClassificationCounts,
)
from app.engine.failure_impact_prediction.validation_summary import (
    build_prediction_validation_summary,
)


def _record(
    experiment_id: str,
    *,
    failed_services: tuple[str, ...] = ("cartservice",),
    failed_workflows: tuple[str, ...] = ("checkout",),
):
    workflow, deployment = get_demo_inputs()
    prediction = predict_failure_impact(workflow, deployment, "node-1")
    observation = ObservedFailureOutcome(
        experiment_id=experiment_id,
        failed_node="node-1",
        failed_services=failed_services,
        failed_workflows=failed_workflows,
    )
    return build_prediction_validation_record(
        prediction,
        observation,
        observation_source="controlled node shutdown",
        observed_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        workflow_freeze_id="checkout-freeze-v1",
        deployment_snapshot_id="node-1-before-failure",
        namespace="research",
    )


def test_summary_preserves_evidence_and_calculates_metrics() -> None:
    exact = _record("experiment-exact")
    missed_service = _record(
        "experiment-missed-service",
        failed_services=("cartservice", "paymentservice"),
    )

    summary = build_prediction_validation_summary(
        "node-1-controlled-failures",
        [exact, missed_service],
    )

    assert summary.batch.batch_id == "node-1-controlled-failures"
    assert summary.batch.records == (exact, missed_service)
    assert summary.batch.exact_match_experiment_ids == (
        "experiment-exact",
    )
    assert summary.batch.service_mismatch_experiment_ids == (
        "experiment-missed-service",
    )
    assert summary.metrics.batch_id == summary.batch.batch_id
    assert summary.metrics.total_experiments == 2
    assert summary.metrics.exact_match_rate == pytest.approx(0.5)
    assert summary.metrics.service_metrics.counts == ClassificationCounts(
        true_positives=2,
        false_positives=0,
        false_negatives=1,
    )
    assert summary.metrics.service_metrics.precision == pytest.approx(1.0)
    assert summary.metrics.service_metrics.recall == pytest.approx(2 / 3)
    assert summary.metrics.service_metrics.f1_score == pytest.approx(0.8)


def test_summary_rejects_empty_experiment_collection() -> None:
    with pytest.raises(ValueError, match="At least one validation record"):
        build_prediction_validation_summary("empty-summary", [])


def test_summary_copies_mutable_record_collection() -> None:
    record = _record("experiment-immutable")
    source_records = [record]

    summary = build_prediction_validation_summary(
        "immutable-summary",
        source_records,
    )
    source_records.append(_record("experiment-later"))

    assert summary.batch.records == (record,)
    assert summary.metrics.total_experiments == 1
