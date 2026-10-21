from datetime import datetime, timezone

import pytest

from app.engine.failure_impact_prediction.captured_experiment import (
    ServiceAvailabilityObservation,
    build_captured_service_evidence,
    parse_service_availability_result,
)
from app.engine.failure_impact_prediction.validation_evidence import (
    assess_validation_evidence,
)


def _captured_evidence(
    *,
    expected_services: tuple[str, ...] = ("paymentservice",),
    observations: tuple[ServiceAvailabilityObservation, ...] = (
        ServiceAvailabilityObservation(
            service="paymentservice",
            available_after_failure=False,
        ),
    ),
):
    return build_captured_service_evidence(
        experiment_id="worker-1-failure",
        failed_node="resilience-research-worker",
        expected_services=expected_services,
        observations=observations,
        observation_source="connected-cluster service probe",
        observed_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
        deployment_snapshot_id="concentrated-baseline",
        namespace="research",
    )


@pytest.mark.parametrize(
    ("result_text", "available"),
    [
        ("SERVICE_AVAILABLE\n", True),
        (" SERVICE_UNAVAILABLE ", False),
    ],
)
def test_service_probe_tokens_are_parsed_explicitly(
    result_text: str,
    available: bool,
) -> None:
    observation = parse_service_availability_result(
        " paymentservice ",
        result_text,
    )

    assert observation.service == "paymentservice"
    assert observation.available_after_failure is available


def test_unknown_service_probe_token_is_rejected() -> None:
    with pytest.raises(ValueError, match="SERVICE_AVAILABLE"):
        parse_service_availability_result("paymentservice", "UNKNOWN")


def test_complete_unavailable_service_capture_preserves_unknown_workflow() -> None:
    captured = _captured_evidence()
    assessment = assess_validation_evidence(captured.evidence)

    assert captured.service_observation_complete is True
    assert captured.expected_services == ("paymentservice",)
    assert captured.observed_services == ("paymentservice",)
    assert captured.missing_services == ()
    assert captured.evidence.failed_services == ("paymentservice",)
    assert captured.evidence.failed_workflows is None
    assert assessment.ready_for_validation is False
    assert assessment.missing_observations == ("failed_workflows",)


def test_complete_available_service_capture_records_observed_no_failure() -> None:
    captured = _captured_evidence(
        observations=(
            ServiceAvailabilityObservation(
                service="paymentservice",
                available_after_failure=True,
            ),
        )
    )

    assert captured.service_observation_complete is True
    assert captured.evidence.failed_services == ()


def test_partial_service_capture_does_not_invent_unobserved_outcomes() -> None:
    captured = _captured_evidence(
        expected_services=("cartservice", "paymentservice"),
    )
    assessment = assess_validation_evidence(captured.evidence)

    assert captured.service_observation_complete is False
    assert captured.missing_services == ("cartservice",)
    assert captured.evidence.failed_services is None
    assert assessment.missing_observations == (
        "failed_services",
        "failed_workflows",
    )


def test_unexpected_or_duplicate_service_observations_are_rejected() -> None:
    with pytest.raises(ValueError, match="unexpected services"):
        _captured_evidence(
            observations=(
                ServiceAvailabilityObservation("other-service", False),
            )
        )

    with pytest.raises(ValueError, match="duplicates"):
        _captured_evidence(
            observations=(
                ServiceAvailabilityObservation("paymentservice", False),
                ServiceAvailabilityObservation("paymentservice", True),
            )
        )


def test_non_boolean_availability_is_rejected() -> None:
    invalid = ServiceAvailabilityObservation(
        service="paymentservice",
        available_after_failure=1,
    )

    with pytest.raises(ValueError, match="must be a boolean"):
        _captured_evidence(observations=(invalid,))
