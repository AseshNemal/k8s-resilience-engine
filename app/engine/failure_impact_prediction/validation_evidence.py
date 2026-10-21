"""Assess whether observed experiment evidence is complete enough to validate."""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.engine.failure_impact_prediction.prediction_validation import (
    ObservedFailureOutcome,
)


class IncompleteValidationEvidenceError(ValueError):
    """Raised when unknown observations would make validation misleading."""

    code = "INCOMPLETE_VALIDATION_EVIDENCE"

    def __init__(
        self,
        experiment_id: str,
        missing_observations: tuple[str, ...],
    ) -> None:
        self.experiment_id = experiment_id
        self.missing_observations = missing_observations
        super().__init__(
            f"Experiment {experiment_id!r} is missing observed evidence for: "
            + ", ".join(missing_observations)
        )


@dataclass(frozen=True)
class ObservedFailureEvidence:
    """Raw observed outcome where ``None`` means the fact was not measured."""

    experiment_id: str
    failed_node: str
    failed_services: tuple[str, ...] | None
    failed_workflows: tuple[str, ...] | None
    observation_source: str
    observed_at: datetime
    workflow_freeze_id: str | None = None
    deployment_snapshot_id: str | None = None
    namespace: str | None = None


@dataclass(frozen=True)
class ValidationEvidenceAssessment:
    """Normalized evidence plus an explicit validation-readiness decision."""

    evidence: ObservedFailureEvidence
    missing_observations: tuple[str, ...]

    @property
    def ready_for_validation(self) -> bool:
        return not self.missing_observations


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _observed_values(
    values: tuple[str, ...] | None,
    field_name: str,
) -> tuple[str, ...] | None:
    if values is None:
        return None

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


def assess_validation_evidence(
    evidence: ObservedFailureEvidence,
) -> ValidationEvidenceAssessment:
    """Normalize evidence and preserve unknown observations as ``None``."""

    if evidence.observed_at.tzinfo is None or evidence.observed_at.utcoffset() is None:
        raise ValueError("observed_at must include timezone information")

    normalized = ObservedFailureEvidence(
        experiment_id=_required_text(evidence.experiment_id, "experiment_id"),
        failed_node=_required_text(evidence.failed_node, "failed_node"),
        failed_services=_observed_values(
            evidence.failed_services,
            "failed_services",
        ),
        failed_workflows=_observed_values(
            evidence.failed_workflows,
            "failed_workflows",
        ),
        observation_source=_required_text(
            evidence.observation_source,
            "observation_source",
        ),
        observed_at=evidence.observed_at.astimezone(timezone.utc),
        workflow_freeze_id=_optional_text(
            evidence.workflow_freeze_id,
            "workflow_freeze_id",
        ),
        deployment_snapshot_id=_optional_text(
            evidence.deployment_snapshot_id,
            "deployment_snapshot_id",
        ),
        namespace=_optional_text(evidence.namespace, "namespace"),
    )
    missing_observations = tuple(
        field_name
        for field_name, value in (
            ("failed_services", normalized.failed_services),
            ("failed_workflows", normalized.failed_workflows),
        )
        if value is None
    )
    return ValidationEvidenceAssessment(
        evidence=normalized,
        missing_observations=missing_observations,
    )


def require_complete_observed_outcome(
    evidence: ObservedFailureEvidence,
) -> ObservedFailureOutcome:
    """Convert only fully observed evidence into a metric-ready outcome."""

    assessment = assess_validation_evidence(evidence)
    if not assessment.ready_for_validation:
        raise IncompleteValidationEvidenceError(
            assessment.evidence.experiment_id,
            assessment.missing_observations,
        )

    failed_services = assessment.evidence.failed_services
    failed_workflows = assessment.evidence.failed_workflows
    if failed_services is None or failed_workflows is None:
        raise AssertionError("complete evidence unexpectedly contained unknown values")

    return ObservedFailureOutcome(
        experiment_id=assessment.evidence.experiment_id,
        failed_node=assessment.evidence.failed_node,
        failed_services=failed_services,
        failed_workflows=failed_workflows,
    )


__all__ = [
    "IncompleteValidationEvidenceError",
    "ObservedFailureEvidence",
    "ValidationEvidenceAssessment",
    "assess_validation_evidence",
    "require_complete_observed_outcome",
]
