from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from app.engine.failure_impact_prediction.validation_evidence import (
    IncompleteValidationEvidenceError,
    ObservedFailureEvidence,
    assess_validation_evidence,
    require_complete_observed_outcome,
)


def _evidence(
    *,
    failed_services: tuple[str, ...] | None = ("paymentservice",),
    failed_workflows: tuple[str, ...] | None = ("checkout",),
) -> ObservedFailureEvidence:
    return ObservedFailureEvidence(
        experiment_id=" worker-1-failure ",
        failed_node=" resilience-research-worker ",
        failed_services=failed_services,
        failed_workflows=failed_workflows,
        observation_source=" connected-cluster experiment capture ",
        observed_at=datetime(
            2026,
            10,
            21,
            10,
            30,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        ),
        workflow_freeze_id=" checkout-freeze-v1 ",
        deployment_snapshot_id=" concentrated-baseline ",
        namespace=" research ",
    )


def test_service_only_capture_is_not_ready_for_validation() -> None:
    assessment = assess_validation_evidence(
        _evidence(failed_workflows=None)
    )

    assert assessment.ready_for_validation is False
    assert assessment.missing_observations == ("failed_workflows",)
    assert assessment.evidence.failed_services == ("paymentservice",)
    assert assessment.evidence.failed_workflows is None


def test_unknown_observation_is_distinct_from_observed_no_failures() -> None:
    unknown = assess_validation_evidence(
        _evidence(failed_services=None, failed_workflows=None)
    )
    observed_no_failures = assess_validation_evidence(
        _evidence(failed_services=(), failed_workflows=())
    )

    assert unknown.ready_for_validation is False
    assert unknown.missing_observations == (
        "failed_services",
        "failed_workflows",
    )
    assert observed_no_failures.ready_for_validation is True
    assert observed_no_failures.evidence.failed_services == ()
    assert observed_no_failures.evidence.failed_workflows == ()


def test_complete_evidence_converts_to_existing_validation_outcome() -> None:
    outcome = require_complete_observed_outcome(
        _evidence(
            failed_services=(" paymentservice ", "cartservice"),
            failed_workflows=("checkout",),
        )
    )

    assert outcome.experiment_id == "worker-1-failure"
    assert outcome.failed_node == "resilience-research-worker"
    assert outcome.failed_services == ("cartservice", "paymentservice")
    assert outcome.failed_workflows == ("checkout",)


def test_incomplete_evidence_is_rejected_before_metric_calculation() -> None:
    with pytest.raises(IncompleteValidationEvidenceError) as captured:
        require_complete_observed_outcome(
            _evidence(failed_workflows=None)
        )

    assert captured.value.code == "INCOMPLETE_VALIDATION_EVIDENCE"
    assert captured.value.experiment_id == "worker-1-failure"
    assert captured.value.missing_observations == ("failed_workflows",)


@pytest.mark.parametrize(
    ("failed_services", "message"),
    [
        (("paymentservice", "paymentservice"), "duplicates"),
        (("paymentservice", "   "), "non-empty"),
    ],
)
def test_known_observations_reject_invalid_values(
    failed_services: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        assess_validation_evidence(
            _evidence(failed_services=failed_services)
        )


def test_assessment_normalizes_provenance_without_mutating_input() -> None:
    evidence = _evidence()

    assessment = assess_validation_evidence(evidence)

    assert assessment.evidence.observed_at == datetime(
        2026,
        10,
        21,
        5,
        0,
        tzinfo=timezone.utc,
    )
    assert assessment.evidence.observation_source == (
        "connected-cluster experiment capture"
    )
    assert assessment.evidence.workflow_freeze_id == "checkout-freeze-v1"
    assert assessment.evidence.deployment_snapshot_id == "concentrated-baseline"
    assert assessment.evidence.namespace == "research"
    assert evidence.experiment_id == " worker-1-failure "


def test_evidence_is_immutable() -> None:
    evidence = _evidence()

    with pytest.raises(FrozenInstanceError):
        evidence.namespace = "changed"
