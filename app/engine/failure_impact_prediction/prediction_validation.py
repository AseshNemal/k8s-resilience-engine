"""Compare predicted failure impact with a controlled experiment outcome."""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.shared.schemas.failure_impact import FailureImpactReport


@dataclass(frozen=True)
class ObservedFailureOutcome:
    """Service and workflow failures observed in one controlled experiment."""

    experiment_id: str
    failed_node: str
    failed_services: tuple[str, ...]
    failed_workflows: tuple[str, ...]


@dataclass(frozen=True)
class PredictionObservationComparison:
    """Set-level differences between one prediction and one observation."""

    experiment_id: str
    failed_node: str
    predicted_failed_services: tuple[str, ...]
    observed_failed_services: tuple[str, ...]
    service_true_positives: tuple[str, ...]
    service_false_positives: tuple[str, ...]
    service_false_negatives: tuple[str, ...]
    predicted_failed_workflows: tuple[str, ...]
    observed_failed_workflows: tuple[str, ...]
    workflow_true_positives: tuple[str, ...]
    workflow_false_positives: tuple[str, ...]
    workflow_false_negatives: tuple[str, ...]
    services_match: bool
    workflows_match: bool
    exact_match: bool


@dataclass(frozen=True)
class PredictionValidationRecord:
    """Traceable comparison record produced from controlled evidence."""

    experiment_id: str
    workflow_id: str
    failed_node: str
    observation_source: str
    observed_at: datetime
    workflow_freeze_id: str | None
    deployment_snapshot_id: str | None
    namespace: str | None
    comparison: PredictionObservationComparison


def _duplicates(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


def _validated_values(
    values: tuple[str, ...],
    field_name: str,
) -> set[str]:
    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError(f"{field_name} must contain non-empty strings")

    duplicates = _duplicates(normalized)
    if duplicates:
        raise ValueError(
            f"{field_name} contains duplicates: {', '.join(duplicates)}"
        )
    return set(normalized)


def compare_prediction_with_observation(
    prediction: FailureImpactReport,
    observation: ObservedFailureOutcome,
) -> PredictionObservationComparison:
    """Compare predicted and observed failures without mutating either input."""

    experiment_id = observation.experiment_id.strip()
    if not experiment_id:
        raise ValueError("experiment_id must be a non-empty string")

    observed_failed_node = observation.failed_node.strip()
    if not observed_failed_node:
        raise ValueError("failed_node must be a non-empty string")
    if prediction.failed_node != observed_failed_node:
        raise ValueError(
            "Prediction and observation must refer to the same failed node: "
            f"{prediction.failed_node} != {observed_failed_node}"
        )

    predicted_services = _validated_values(
        tuple(prediction.failed_services),
        "prediction.failed_services",
    )
    observed_services = _validated_values(
        observation.failed_services,
        "observation.failed_services",
    )
    predicted_workflows = _validated_values(
        tuple(prediction.failed_workflows),
        "prediction.failed_workflows",
    )
    observed_workflows = _validated_values(
        observation.failed_workflows,
        "observation.failed_workflows",
    )

    services_match = predicted_services == observed_services
    workflows_match = predicted_workflows == observed_workflows

    return PredictionObservationComparison(
        experiment_id=experiment_id,
        failed_node=observed_failed_node,
        predicted_failed_services=tuple(sorted(predicted_services)),
        observed_failed_services=tuple(sorted(observed_services)),
        service_true_positives=tuple(
            sorted(predicted_services & observed_services)
        ),
        service_false_positives=tuple(
            sorted(predicted_services - observed_services)
        ),
        service_false_negatives=tuple(
            sorted(observed_services - predicted_services)
        ),
        predicted_failed_workflows=tuple(sorted(predicted_workflows)),
        observed_failed_workflows=tuple(sorted(observed_workflows)),
        workflow_true_positives=tuple(
            sorted(predicted_workflows & observed_workflows)
        ),
        workflow_false_positives=tuple(
            sorted(predicted_workflows - observed_workflows)
        ),
        workflow_false_negatives=tuple(
            sorted(observed_workflows - predicted_workflows)
        ),
        services_match=services_match,
        workflows_match=workflows_match,
        exact_match=services_match and workflows_match,
    )


def _optional_provenance(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty when provided")
    return normalized


def build_prediction_validation_record(
    prediction: FailureImpactReport,
    observation: ObservedFailureOutcome,
    *,
    observation_source: str,
    observed_at: datetime,
    workflow_freeze_id: str | None = None,
    deployment_snapshot_id: str | None = None,
    namespace: str | None = None,
) -> PredictionValidationRecord:
    """Build a timestamped validation record without persisting it."""

    normalized_source = observation_source.strip()
    if not normalized_source:
        raise ValueError("observation_source must be a non-empty string")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must include timezone information")

    comparison = compare_prediction_with_observation(
        prediction,
        observation,
    )
    return PredictionValidationRecord(
        experiment_id=comparison.experiment_id,
        workflow_id=prediction.workflow_id,
        failed_node=comparison.failed_node,
        observation_source=normalized_source,
        observed_at=observed_at.astimezone(timezone.utc),
        workflow_freeze_id=_optional_provenance(
            workflow_freeze_id,
            "workflow_freeze_id",
        ),
        deployment_snapshot_id=_optional_provenance(
            deployment_snapshot_id,
            "deployment_snapshot_id",
        ),
        namespace=_optional_provenance(namespace, "namespace"),
        comparison=comparison,
    )


__all__ = [
    "ObservedFailureOutcome",
    "PredictionObservationComparison",
    "PredictionValidationRecord",
    "build_prediction_validation_record",
    "compare_prediction_with_observation",
]
