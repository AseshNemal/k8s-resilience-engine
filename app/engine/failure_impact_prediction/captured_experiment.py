"""Adapt captured service and workflow results into validation evidence."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.engine.failure_impact_prediction.validation_evidence import (
    ObservedFailureEvidence,
    assess_validation_evidence,
)


@dataclass(frozen=True)
class ServiceAvailabilityObservation:
    """Observed availability of one expected service after node failure."""

    service: str
    available_after_failure: bool


@dataclass(frozen=True)
class CapturedServiceEvidence:
    """Adapted evidence with explicit service-observation coverage."""

    evidence: ObservedFailureEvidence
    expected_services: tuple[str, ...]
    observed_services: tuple[str, ...]
    missing_services: tuple[str, ...]

    @property
    def service_observation_complete(self) -> bool:
        return not self.missing_services


@dataclass(frozen=True)
class WorkflowExecutionObservation:
    """Observed success of one expected workflow after node failure."""

    workflow_id: str
    succeeded_after_failure: bool


@dataclass(frozen=True)
class CapturedWorkflowEvidence:
    """Adapted evidence with explicit workflow-observation coverage."""

    evidence: ObservedFailureEvidence
    expected_workflows: tuple[str, ...]
    observed_workflows: tuple[str, ...]
    missing_workflows: tuple[str, ...]

    @property
    def workflow_observation_complete(self) -> bool:
        return not self.missing_workflows


def parse_service_availability_result(
    service: str,
    result_text: str,
) -> ServiceAvailabilityObservation:
    """Parse the explicit tokens written by a captured service probe."""

    normalized_service = service.strip()
    if not normalized_service:
        raise ValueError("service must be a non-empty string")

    result = result_text.strip()
    if result == "SERVICE_AVAILABLE":
        available = True
    elif result == "SERVICE_UNAVAILABLE":
        available = False
    else:
        raise ValueError(
            "service result must be SERVICE_AVAILABLE or SERVICE_UNAVAILABLE"
        )
    return ServiceAvailabilityObservation(
        service=normalized_service,
        available_after_failure=available,
    )


def parse_workflow_execution_result(
    workflow_id: str,
    result_text: str,
) -> WorkflowExecutionObservation:
    """Parse the explicit tokens written by a captured workflow probe."""

    normalized_workflow = workflow_id.strip()
    if not normalized_workflow:
        raise ValueError("workflow_id must be a non-empty string")

    result = result_text.strip()
    if result == "WORKFLOW_SUCCEEDED":
        succeeded = True
    elif result == "WORKFLOW_FAILED":
        succeeded = False
    else:
        raise ValueError(
            "workflow result must be WORKFLOW_SUCCEEDED or WORKFLOW_FAILED"
        )
    return WorkflowExecutionObservation(
        workflow_id=normalized_workflow,
        succeeded_after_failure=succeeded,
    )


def _identifiers(
    values: Sequence[str],
    field_name: str,
) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError(f"{field_name} must contain non-empty strings")
    duplicates = tuple(
        sorted({value for value in normalized if normalized.count(value) > 1})
    )
    if duplicates:
        raise ValueError(
            f"{field_name} contains duplicates: {', '.join(duplicates)}"
        )
    return tuple(sorted(normalized))


def build_captured_service_evidence(
    *,
    experiment_id: str,
    failed_node: str,
    expected_services: Sequence[str],
    observations: Sequence[ServiceAvailabilityObservation],
    observation_source: str,
    observed_at: datetime,
    workflow_freeze_id: str | None = None,
    deployment_snapshot_id: str | None = None,
    namespace: str | None = None,
) -> CapturedServiceEvidence:
    """Build evidence without treating partial service coverage as complete."""

    normalized_expected = _identifiers(
        expected_services,
        "expected_services",
    )
    if not normalized_expected:
        raise ValueError("expected_services must contain at least one service")

    normalized_observation_names = _identifiers(
        tuple(observation.service for observation in observations),
        "observations",
    )
    expected_set = set(normalized_expected)
    unexpected = tuple(
        sorted(set(normalized_observation_names) - expected_set)
    )
    if unexpected:
        raise ValueError(
            "observations contain unexpected services: "
            + ", ".join(unexpected)
        )

    availability: dict[str, bool] = {}
    for observation in observations:
        service = observation.service.strip()
        if type(observation.available_after_failure) is not bool:
            raise ValueError("available_after_failure must be a boolean")
        availability[service] = observation.available_after_failure

    observed_services = tuple(sorted(availability))
    missing_services = tuple(
        service for service in normalized_expected if service not in availability
    )
    failed_services = (
        tuple(
            service
            for service in normalized_expected
            if not availability[service]
        )
        if not missing_services
        else None
    )
    assessment = assess_validation_evidence(
        ObservedFailureEvidence(
            experiment_id=experiment_id,
            failed_node=failed_node,
            failed_services=failed_services,
            failed_workflows=None,
            observation_source=observation_source,
            observed_at=observed_at,
            workflow_freeze_id=workflow_freeze_id,
            deployment_snapshot_id=deployment_snapshot_id,
            namespace=namespace,
        )
    )
    return CapturedServiceEvidence(
        evidence=assessment.evidence,
        expected_services=normalized_expected,
        observed_services=observed_services,
        missing_services=missing_services,
    )


def build_captured_workflow_evidence(
    *,
    experiment_id: str,
    failed_node: str,
    expected_workflows: Sequence[str],
    observations: Sequence[WorkflowExecutionObservation],
    observation_source: str,
    observed_at: datetime,
    workflow_freeze_id: str | None = None,
    deployment_snapshot_id: str | None = None,
    namespace: str | None = None,
) -> CapturedWorkflowEvidence:
    """Build evidence without treating partial workflow coverage as complete."""

    normalized_expected = _identifiers(
        expected_workflows,
        "expected_workflows",
    )
    if not normalized_expected:
        raise ValueError("expected_workflows must contain at least one workflow")

    normalized_observation_ids = _identifiers(
        tuple(observation.workflow_id for observation in observations),
        "observations",
    )
    expected_set = set(normalized_expected)
    unexpected = tuple(
        sorted(set(normalized_observation_ids) - expected_set)
    )
    if unexpected:
        raise ValueError(
            "observations contain unexpected workflows: "
            + ", ".join(unexpected)
        )

    successes: dict[str, bool] = {}
    for observation in observations:
        workflow_id = observation.workflow_id.strip()
        if type(observation.succeeded_after_failure) is not bool:
            raise ValueError("succeeded_after_failure must be a boolean")
        successes[workflow_id] = observation.succeeded_after_failure

    observed_workflows = tuple(sorted(successes))
    missing_workflows = tuple(
        workflow_id
        for workflow_id in normalized_expected
        if workflow_id not in successes
    )
    failed_workflows = (
        tuple(
            workflow_id
            for workflow_id in normalized_expected
            if not successes[workflow_id]
        )
        if not missing_workflows
        else None
    )
    assessment = assess_validation_evidence(
        ObservedFailureEvidence(
            experiment_id=experiment_id,
            failed_node=failed_node,
            failed_services=None,
            failed_workflows=failed_workflows,
            observation_source=observation_source,
            observed_at=observed_at,
            workflow_freeze_id=workflow_freeze_id,
            deployment_snapshot_id=deployment_snapshot_id,
            namespace=namespace,
        )
    )
    return CapturedWorkflowEvidence(
        evidence=assessment.evidence,
        expected_workflows=normalized_expected,
        observed_workflows=observed_workflows,
        missing_workflows=missing_workflows,
    )


__all__ = [
    "CapturedServiceEvidence",
    "CapturedWorkflowEvidence",
    "ServiceAvailabilityObservation",
    "WorkflowExecutionObservation",
    "build_captured_service_evidence",
    "build_captured_workflow_evidence",
    "parse_service_availability_result",
    "parse_workflow_execution_result",
]
