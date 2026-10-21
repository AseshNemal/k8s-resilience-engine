from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.engine.failure_impact_prediction.captured_evidence_combiner import (
    CapturedEvidenceMismatchError,
    IncompleteCapturedExperimentError,
    combine_captured_experiment_evidence,
)
from app.engine.failure_impact_prediction.captured_experiment import (
    ServiceAvailabilityObservation,
    WorkflowExecutionObservation,
    build_captured_service_evidence,
    build_captured_workflow_evidence,
)
from app.engine.failure_impact_prediction.validation_evidence import (
    assess_validation_evidence,
    require_complete_observed_outcome,
)


OBSERVED_AT = datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc)


def _service_capture(
    *,
    expected_services: tuple[str, ...] = ("paymentservice",),
    observations: tuple[ServiceAvailabilityObservation, ...] = (
        ServiceAvailabilityObservation("paymentservice", False),
    ),
):
    return build_captured_service_evidence(
        experiment_id="worker-1-failure",
        failed_node="resilience-research-worker",
        expected_services=expected_services,
        observations=observations,
        observation_source="connected-cluster experiment",
        observed_at=OBSERVED_AT,
        workflow_freeze_id="checkout-freeze-v1",
        deployment_snapshot_id="concentrated-baseline",
        namespace="research",
    )


def _workflow_capture(
    *,
    expected_workflows: tuple[str, ...] = ("checkout",),
    observations: tuple[WorkflowExecutionObservation, ...] = (
        WorkflowExecutionObservation("checkout", False),
    ),
):
    return build_captured_workflow_evidence(
        experiment_id="worker-1-failure",
        failed_node="resilience-research-worker",
        expected_workflows=expected_workflows,
        observations=observations,
        observation_source="connected-cluster experiment",
        observed_at=OBSERVED_AT,
        workflow_freeze_id="checkout-freeze-v1",
        deployment_snapshot_id="concentrated-baseline",
        namespace="research",
    )


def test_complete_matching_captures_become_metric_ready() -> None:
    evidence = combine_captured_experiment_evidence(
        _service_capture(),
        _workflow_capture(),
    )
    assessment = assess_validation_evidence(evidence)
    outcome = require_complete_observed_outcome(evidence)

    assert assessment.ready_for_validation is True
    assert assessment.missing_observations == ()
    assert outcome.experiment_id == "worker-1-failure"
    assert outcome.failed_node == "resilience-research-worker"
    assert outcome.failed_services == ("paymentservice",)
    assert outcome.failed_workflows == ("checkout",)


def test_observed_successes_remain_empty_failure_sets() -> None:
    service_capture = _service_capture(
        observations=(
            ServiceAvailabilityObservation("paymentservice", True),
        )
    )
    workflow_capture = _workflow_capture(
        observations=(
            WorkflowExecutionObservation("checkout", True),
        )
    )

    evidence = combine_captured_experiment_evidence(
        service_capture,
        workflow_capture,
    )

    assert evidence.failed_services == ()
    assert evidence.failed_workflows == ()


def test_partial_capture_is_rejected_before_combination() -> None:
    service_capture = _service_capture(
        expected_services=("cartservice", "paymentservice"),
    )
    workflow_capture = _workflow_capture(
        expected_workflows=("checkout", "refund"),
    )

    with pytest.raises(IncompleteCapturedExperimentError) as captured:
        combine_captured_experiment_evidence(
            service_capture,
            workflow_capture,
        )

    assert captured.value.code == "INCOMPLETE_CAPTURED_EXPERIMENT"
    assert captured.value.missing_services == ("cartservice",)
    assert captured.value.missing_workflows == ("refund",)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("experiment_id", "different-experiment"),
        ("failed_node", "different-node"),
        ("observation_source", "different-source"),
        ("observed_at", OBSERVED_AT + timedelta(seconds=1)),
        ("workflow_freeze_id", "different-freeze"),
        ("deployment_snapshot_id", "different-snapshot"),
        ("namespace", "different-namespace"),
    ],
)
def test_mismatched_capture_provenance_is_rejected(
    field_name: str,
    replacement: object,
) -> None:
    service_capture = _service_capture()
    workflow_capture = _workflow_capture()
    mismatched_workflow = replace(
        workflow_capture,
        evidence=replace(
            workflow_capture.evidence,
            **{field_name: replacement},
        ),
    )

    with pytest.raises(CapturedEvidenceMismatchError) as captured:
        combine_captured_experiment_evidence(
            service_capture,
            mismatched_workflow,
        )

    assert captured.value.code == "MISMATCHED_CAPTURED_EVIDENCE"
    assert captured.value.mismatched_fields == (field_name,)


def test_combination_does_not_modify_source_captures() -> None:
    service_capture = _service_capture()
    workflow_capture = _workflow_capture()

    combine_captured_experiment_evidence(
        service_capture,
        workflow_capture,
    )

    assert service_capture.evidence.failed_workflows is None
    assert workflow_capture.evidence.failed_services is None
