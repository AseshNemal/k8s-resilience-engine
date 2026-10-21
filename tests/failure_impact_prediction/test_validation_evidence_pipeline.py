from datetime import datetime, timedelta, timezone

import pytest

from app.engine.failure_impact_prediction.captured_evidence_combiner import (
    combine_captured_experiment_evidence,
)
from app.engine.failure_impact_prediction.captured_experiment import (
    ServiceAvailabilityObservation,
    WorkflowExecutionObservation,
    build_captured_service_evidence,
    build_captured_workflow_evidence,
)
from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.failure_impact_prediction.validation_evidence import (
    IncompleteValidationEvidenceError,
    ObservedFailureEvidence,
)
from app.engine.failure_impact_prediction.validation_metrics import (
    ClassificationCounts,
)
from app.engine.failure_impact_prediction.validation_pipeline import (
    PredictionEvidenceExperiment,
    evaluate_prediction_evidence,
)


OBSERVED_AT = datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc)


def _prediction():
    workflow, deployment = get_demo_inputs()
    return predict_failure_impact(workflow, deployment, "node-1")


def _combined_evidence(
    experiment_id: str,
    *,
    failed_node: str = "node-1",
    service_observations: tuple[ServiceAvailabilityObservation, ...] = (
        ServiceAvailabilityObservation("cartservice", False),
    ),
    expected_services: tuple[str, ...] = ("cartservice",),
    workflow_observations: tuple[WorkflowExecutionObservation, ...] = (
        WorkflowExecutionObservation("checkout", False),
    ),
):
    common = {
        "experiment_id": experiment_id,
        "failed_node": failed_node,
        "observation_source": "connected-cluster experiment",
        "observed_at": OBSERVED_AT,
        "workflow_freeze_id": "checkout-freeze-v1",
        "deployment_snapshot_id": "deployment-before-node-1",
        "namespace": "research",
    }
    service_capture = build_captured_service_evidence(
        expected_services=expected_services,
        observations=service_observations,
        **common,
    )
    workflow_capture = build_captured_workflow_evidence(
        expected_workflows=("checkout",),
        observations=workflow_observations,
        **common,
    )
    return combine_captured_experiment_evidence(
        service_capture,
        workflow_capture,
    )


def test_real_evidence_pipeline_builds_summary_and_preserves_provenance() -> None:
    evidence = _combined_evidence("experiment-exact")

    summary = evaluate_prediction_evidence(
        "real-node-1-validation",
        [PredictionEvidenceExperiment(_prediction(), evidence)],
    )

    assert summary.metrics.total_experiments == 1
    assert summary.metrics.exact_match_count == 1
    assert summary.metrics.exact_match_rate == pytest.approx(1.0)
    record = summary.batch.records[0]
    assert record.experiment_id == "experiment-exact"
    assert record.observation_source == "connected-cluster experiment"
    assert record.observed_at == OBSERVED_AT
    assert record.workflow_freeze_id == "checkout-freeze-v1"
    assert record.deployment_snapshot_id == "deployment-before-node-1"
    assert record.namespace == "research"


def test_real_evidence_pipeline_aggregates_repeated_experiments() -> None:
    exact = _combined_evidence("experiment-exact")
    missed_service = _combined_evidence(
        "experiment-missed-service",
        expected_services=("cartservice", "paymentservice"),
        service_observations=(
            ServiceAvailabilityObservation("cartservice", False),
            ServiceAvailabilityObservation("paymentservice", False),
        ),
    )

    summary = evaluate_prediction_evidence(
        "repeated-real-experiments",
        [
            PredictionEvidenceExperiment(_prediction(), exact),
            PredictionEvidenceExperiment(_prediction(), missed_service),
        ],
    )

    assert summary.metrics.total_experiments == 2
    assert summary.metrics.exact_match_count == 1
    assert summary.metrics.service_metrics.counts == ClassificationCounts(
        true_positives=2,
        false_positives=0,
        false_negatives=1,
    )
    assert summary.metrics.service_metrics.precision == pytest.approx(1.0)
    assert summary.metrics.service_metrics.recall == pytest.approx(2 / 3)
    assert summary.metrics.service_metrics.f1_score == pytest.approx(0.8)


def test_incomplete_real_evidence_is_blocked_before_summary_creation() -> None:
    incomplete = ObservedFailureEvidence(
        experiment_id="experiment-incomplete",
        failed_node="node-1",
        failed_services=("cartservice",),
        failed_workflows=None,
        observation_source="service-only capture",
        observed_at=OBSERVED_AT,
    )

    with pytest.raises(IncompleteValidationEvidenceError):
        evaluate_prediction_evidence(
            "invalid-evidence",
            [PredictionEvidenceExperiment(_prediction(), incomplete)],
        )


def test_direct_real_evidence_is_normalized_before_record_creation() -> None:
    sri_lanka_time = timezone(timedelta(hours=5, minutes=30))
    evidence = ObservedFailureEvidence(
        experiment_id=" experiment-normalized ",
        failed_node=" node-1 ",
        failed_services=(" cartservice ",),
        failed_workflows=(" checkout ",),
        observation_source=" connected-cluster experiment ",
        observed_at=datetime(2026, 10, 21, 10, 30, tzinfo=sri_lanka_time),
        workflow_freeze_id=" checkout-freeze-v1 ",
        deployment_snapshot_id=" deployment-before-node-1 ",
        namespace=" research ",
    )

    summary = evaluate_prediction_evidence(
        "normalized-evidence",
        [PredictionEvidenceExperiment(_prediction(), evidence)],
    )

    record = summary.batch.records[0]
    assert record.experiment_id == "experiment-normalized"
    assert record.failed_node == "node-1"
    assert record.observation_source == "connected-cluster experiment"
    assert record.observed_at == OBSERVED_AT
    assert record.workflow_freeze_id == "checkout-freeze-v1"
    assert record.deployment_snapshot_id == "deployment-before-node-1"
    assert record.namespace == "research"


def test_prediction_and_evidence_must_refer_to_same_failed_node() -> None:
    evidence = _combined_evidence(
        "experiment-wrong-node",
        failed_node="node-2",
        service_observations=(
            ServiceAvailabilityObservation("cartservice", True),
        ),
        workflow_observations=(
            WorkflowExecutionObservation("checkout", True),
        ),
    )

    with pytest.raises(ValueError, match="same failed node"):
        evaluate_prediction_evidence(
            "wrong-node-evidence",
            [PredictionEvidenceExperiment(_prediction(), evidence)],
        )


def test_real_evidence_pipeline_rejects_empty_experiment_collection() -> None:
    with pytest.raises(ValueError, match="prediction evidence experiment"):
        evaluate_prediction_evidence("empty-real-evidence", [])
