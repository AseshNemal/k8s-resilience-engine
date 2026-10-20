"""Compare predicted failure impact with a controlled experiment outcome."""

from dataclasses import dataclass

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


__all__ = [
    "ObservedFailureOutcome",
    "PredictionObservationComparison",
    "compare_prediction_with_observation",
]
