"""Combine complete service and workflow captures from the same experiment."""

from app.engine.failure_impact_prediction.captured_experiment import (
    CapturedServiceEvidence,
    CapturedWorkflowEvidence,
)
from app.engine.failure_impact_prediction.validation_evidence import (
    ObservedFailureEvidence,
    assess_validation_evidence,
)


class CapturedEvidenceMismatchError(ValueError):
    """Raised when service and workflow evidence came from different runs."""

    code = "MISMATCHED_CAPTURED_EVIDENCE"

    def __init__(self, mismatched_fields: tuple[str, ...]) -> None:
        self.mismatched_fields = mismatched_fields
        super().__init__(
            "Service and workflow captures differ for: "
            + ", ".join(mismatched_fields)
        )


class IncompleteCapturedExperimentError(ValueError):
    """Raised when either capture does not cover its expected targets."""

    code = "INCOMPLETE_CAPTURED_EXPERIMENT"

    def __init__(
        self,
        missing_services: tuple[str, ...],
        missing_workflows: tuple[str, ...],
    ) -> None:
        self.missing_services = missing_services
        self.missing_workflows = missing_workflows
        missing: list[str] = []
        if missing_services:
            missing.append("services=" + ",".join(missing_services))
        if missing_workflows:
            missing.append("workflows=" + ",".join(missing_workflows))
        super().__init__("Captured experiment is incomplete: " + "; ".join(missing))


_MATCHING_PROVENANCE_FIELDS = (
    "experiment_id",
    "failed_node",
    "observation_source",
    "observed_at",
    "workflow_freeze_id",
    "deployment_snapshot_id",
    "namespace",
)


def combine_captured_experiment_evidence(
    service_capture: CapturedServiceEvidence,
    workflow_capture: CapturedWorkflowEvidence,
) -> ObservedFailureEvidence:
    """Join complete captures only when their experiment provenance matches."""

    service_evidence = service_capture.evidence
    workflow_evidence = workflow_capture.evidence
    mismatched_fields = tuple(
        field_name
        for field_name in _MATCHING_PROVENANCE_FIELDS
        if getattr(service_evidence, field_name)
        != getattr(workflow_evidence, field_name)
    )
    if mismatched_fields:
        raise CapturedEvidenceMismatchError(mismatched_fields)

    if (
        not service_capture.service_observation_complete
        or not workflow_capture.workflow_observation_complete
    ):
        raise IncompleteCapturedExperimentError(
            service_capture.missing_services,
            workflow_capture.missing_workflows,
        )

    failed_services = service_evidence.failed_services
    failed_workflows = workflow_evidence.failed_workflows
    if failed_services is None or failed_workflows is None:
        raise AssertionError("complete captures unexpectedly contained unknown outcomes")

    assessment = assess_validation_evidence(
        ObservedFailureEvidence(
            experiment_id=service_evidence.experiment_id,
            failed_node=service_evidence.failed_node,
            failed_services=failed_services,
            failed_workflows=failed_workflows,
            observation_source=service_evidence.observation_source,
            observed_at=service_evidence.observed_at,
            workflow_freeze_id=service_evidence.workflow_freeze_id,
            deployment_snapshot_id=service_evidence.deployment_snapshot_id,
            namespace=service_evidence.namespace,
        )
    )
    if not assessment.ready_for_validation:
        raise AssertionError("combined evidence unexpectedly remained incomplete")
    return assessment.evidence


__all__ = [
    "CapturedEvidenceMismatchError",
    "IncompleteCapturedExperimentError",
    "combine_captured_experiment_evidence",
]
