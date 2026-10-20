from datetime import datetime, timezone

import pytest

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
    PredictionValidationRecord,
    build_prediction_validation_record,
)
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.failure_impact_prediction.validation_batch import (
    build_prediction_validation_batch,
)


def _prediction():
    workflow, deployment = get_demo_inputs()
    return predict_failure_impact(workflow, deployment, "node-1")


def _record(
    experiment_id: str,
    *,
    failed_services: tuple[str, ...] = ("cartservice",),
    failed_workflows: tuple[str, ...] = ("checkout",),
) -> PredictionValidationRecord:
    observation = ObservedFailureOutcome(
        experiment_id=experiment_id,
        failed_node="node-1",
        failed_services=failed_services,
        failed_workflows=failed_workflows,
    )
    return build_prediction_validation_record(
        _prediction(),
        observation,
        observation_source="controlled experiment",
        observed_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )


def test_batch_groups_exact_service_and_workflow_outcomes() -> None:
    exact = _record("experiment-exact")
    service_mismatch = _record(
        "experiment-service-mismatch",
        failed_services=("cartservice", "paymentservice"),
    )
    workflow_mismatch = _record(
        "experiment-workflow-mismatch",
        failed_workflows=(),
    )

    batch = build_prediction_validation_batch(
        " node-1-repeatability ",
        [exact, service_mismatch, workflow_mismatch],
    )

    assert batch.batch_id == "node-1-repeatability"
    assert batch.total_experiments == 3
    assert batch.records == (
        exact,
        service_mismatch,
        workflow_mismatch,
    )
    assert batch.exact_match_experiment_ids == ("experiment-exact",)
    assert batch.service_mismatch_experiment_ids == (
        "experiment-service-mismatch",
    )
    assert batch.workflow_mismatch_experiment_ids == (
        "experiment-workflow-mismatch",
    )


def test_batch_rejects_empty_records() -> None:
    with pytest.raises(ValueError, match="At least one validation record"):
        build_prediction_validation_batch("empty-batch", [])


def test_batch_rejects_blank_identity() -> None:
    with pytest.raises(ValueError, match="batch_id"):
        build_prediction_validation_batch("   ", [_record("experiment-1")])


def test_batch_rejects_duplicate_experiment_ids() -> None:
    first = _record("experiment-duplicate")
    second = _record("experiment-duplicate")

    with pytest.raises(
        ValueError,
        match="Duplicate validation experiment IDs: experiment-duplicate",
    ):
        build_prediction_validation_batch(
            "duplicate-batch",
            [first, second],
        )


def test_batch_copies_mutable_record_sequence() -> None:
    first = _record("experiment-1")
    source_records = [first]

    batch = build_prediction_validation_batch(
        "immutable-batch",
        source_records,
    )
    source_records.append(_record("experiment-2"))

    assert batch.records == (first,)
    assert batch.total_experiments == 1
