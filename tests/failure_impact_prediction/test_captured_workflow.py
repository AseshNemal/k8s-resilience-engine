from datetime import datetime, timezone

import pytest

from app.engine.failure_impact_prediction.captured_experiment import (
    WorkflowExecutionObservation,
    build_captured_workflow_evidence,
    parse_workflow_execution_result,
)
from app.engine.failure_impact_prediction.validation_evidence import (
    assess_validation_evidence,
)


def _captured_evidence(
    *,
    expected_workflows: tuple[str, ...] = ("checkout",),
    observations: tuple[WorkflowExecutionObservation, ...] = (
        WorkflowExecutionObservation(
            workflow_id="checkout",
            succeeded_after_failure=False,
        ),
    ),
):
    return build_captured_workflow_evidence(
        experiment_id="worker-1-failure",
        failed_node="resilience-research-worker",
        expected_workflows=expected_workflows,
        observations=observations,
        observation_source="connected-cluster workflow probe",
        observed_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
        workflow_freeze_id="checkout-freeze-v1",
        deployment_snapshot_id="concentrated-baseline",
        namespace="research",
    )


@pytest.mark.parametrize(
    ("result_text", "succeeded"),
    [
        ("WORKFLOW_SUCCEEDED\n", True),
        (" WORKFLOW_FAILED ", False),
    ],
)
def test_workflow_probe_tokens_are_parsed_explicitly(
    result_text: str,
    succeeded: bool,
) -> None:
    observation = parse_workflow_execution_result(
        " checkout ",
        result_text,
    )

    assert observation.workflow_id == "checkout"
    assert observation.succeeded_after_failure is succeeded


def test_unknown_workflow_probe_token_is_rejected() -> None:
    with pytest.raises(ValueError, match="WORKFLOW_SUCCEEDED"):
        parse_workflow_execution_result("checkout", "UNKNOWN")


def test_complete_failed_workflow_capture_preserves_unknown_services() -> None:
    captured = _captured_evidence()
    assessment = assess_validation_evidence(captured.evidence)

    assert captured.workflow_observation_complete is True
    assert captured.expected_workflows == ("checkout",)
    assert captured.observed_workflows == ("checkout",)
    assert captured.missing_workflows == ()
    assert captured.evidence.failed_services is None
    assert captured.evidence.failed_workflows == ("checkout",)
    assert assessment.ready_for_validation is False
    assert assessment.missing_observations == ("failed_services",)


def test_complete_successful_workflow_capture_records_observed_no_failure() -> None:
    captured = _captured_evidence(
        observations=(
            WorkflowExecutionObservation(
                workflow_id="checkout",
                succeeded_after_failure=True,
            ),
        )
    )

    assert captured.workflow_observation_complete is True
    assert captured.evidence.failed_workflows == ()


def test_partial_workflow_capture_does_not_invent_unobserved_outcomes() -> None:
    captured = _captured_evidence(
        expected_workflows=("checkout", "refund"),
    )
    assessment = assess_validation_evidence(captured.evidence)

    assert captured.workflow_observation_complete is False
    assert captured.missing_workflows == ("refund",)
    assert captured.evidence.failed_workflows is None
    assert assessment.missing_observations == (
        "failed_services",
        "failed_workflows",
    )


def test_unexpected_or_duplicate_workflow_observations_are_rejected() -> None:
    with pytest.raises(ValueError, match="unexpected workflows"):
        _captured_evidence(
            observations=(
                WorkflowExecutionObservation("refund", False),
            )
        )

    with pytest.raises(ValueError, match="duplicates"):
        _captured_evidence(
            observations=(
                WorkflowExecutionObservation("checkout", False),
                WorkflowExecutionObservation("checkout", True),
            )
        )


def test_non_boolean_workflow_result_is_rejected() -> None:
    invalid = WorkflowExecutionObservation(
        workflow_id="checkout",
        succeeded_after_failure=1,
    )

    with pytest.raises(ValueError, match="must be a boolean"):
        _captured_evidence(observations=(invalid,))
