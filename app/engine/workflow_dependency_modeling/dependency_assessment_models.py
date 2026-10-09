"""Immutable evidence and decision models for dependency validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.shared.schemas.workflow import WorkflowGraph


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class InterventionType(str, Enum):
    SERVICE_UNAVAILABLE = "service_unavailable"
    REQUEST_REJECTED = "request_rejected"
    TIMEOUT_INJECTED = "timeout_injected"
    CALL_OMITTED = "call_omitted"
    DEGRADED_RESPONSE = "degraded_response"


class ValidatedDependencyType(str, Enum):
    MANDATORY = "mandatory"
    OPTIONAL = "optional"
    SUPPORTING = "supporting"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"Dependency assessment requires non-empty '{key}'")
    return value.strip()


def _identifier(data: Mapping[str, Any], key: str) -> str:
    value = _required_text(data, key)
    if not _IDENTIFIER.fullmatch(value):
        raise ComponentDataError(f"Dependency assessment '{key}' has an invalid format")
    return value


def _timestamp(value: Any, key: str) -> datetime:
    if not isinstance(value, str):
        raise ComponentDataError(f"Dependency assessment '{key}' must be ISO 8601")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ComponentDataError(
            f"Dependency assessment '{key}' must be ISO 8601"
        ) from err
    if result.tzinfo is None:
        raise ComponentDataError(
            f"Dependency assessment '{key}' must include a timezone"
        )
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _batch_ids(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ComponentDataError(f"Dependency assessment '{key}' must be a non-empty list")
    result = tuple(str(item).strip() for item in value)
    if any(not item or not _IDENTIFIER.fullmatch(item) for item in result):
        raise ComponentDataError(f"Dependency assessment '{key}' contains an invalid ID")
    if len(result) != len(set(result)):
        raise ComponentDataError(f"Dependency assessment '{key}' contains duplicates")
    return result


@dataclass(frozen=True)
class AssessmentPolicy:
    """Conservative, reproducible thresholds for one assessment."""

    min_samples_per_cohort: int = 3
    min_baseline_success_rate: float = 0.8
    optional_effect_tolerance: float = 0.1
    mandatory_effect_threshold: float = 0.5
    max_mandatory_success_rate: float = 0.2

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "AssessmentPolicy":
        payload = data or {}
        sample_count = payload.get("minSamplesPerCohort", 3)
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 1:
            raise ComponentDataError("minSamplesPerCohort must be a positive integer")
        values = {
            "min_baseline_success_rate": payload.get("minBaselineSuccessRate", 0.8),
            "optional_effect_tolerance": payload.get("optionalEffectTolerance", 0.1),
            "mandatory_effect_threshold": payload.get("mandatoryEffectThreshold", 0.5),
            "max_mandatory_success_rate": payload.get("maxMandatorySuccessRate", 0.2),
        }
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ComponentDataError(f"{name} must be numeric")
            if not 0.0 <= float(value) <= 1.0:
                raise ComponentDataError(f"{name} must be between 0 and 1")
        if float(values["optional_effect_tolerance"]) >= float(
            values["mandatory_effect_threshold"]
        ):
            raise ComponentDataError(
                "optionalEffectTolerance must be below mandatoryEffectThreshold"
            )
        return cls(
            min_samples_per_cohort=sample_count,
            **{name: float(value) for name, value in values.items()},
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "minSamplesPerCohort": self.min_samples_per_cohort,
            "minBaselineSuccessRate": self.min_baseline_success_rate,
            "optionalEffectTolerance": self.optional_effect_tolerance,
            "mandatoryEffectThreshold": self.mandatory_effect_threshold,
            "maxMandatorySuccessRate": self.max_mandatory_success_rate,
        }


@dataclass(frozen=True)
class InterventionCohort:
    target_service: str
    intervention_type: InterventionType
    batch_ids: tuple[str, ...]
    intervention_confirmed: bool
    confirmation_evidence: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InterventionCohort":
        try:
            intervention_type = InterventionType(data.get("interventionType"))
        except ValueError as err:
            raise ComponentDataError("interventionType is not supported") from err
        confirmed = data.get("interventionConfirmed")
        if not isinstance(confirmed, bool):
            raise ComponentDataError("interventionConfirmed must be boolean")
        evidence = data.get("confirmationEvidence")
        if confirmed and (not isinstance(evidence, str) or not evidence.strip()):
            raise ComponentDataError(
                "Confirmed interventions require confirmationEvidence"
            )
        if evidence is not None and not isinstance(evidence, str):
            raise ComponentDataError("confirmationEvidence must be text")
        return cls(
            target_service=_identifier(data, "targetService"),
            intervention_type=intervention_type,
            batch_ids=_batch_ids(data.get("batchIds"), "batchIds"),
            intervention_confirmed=confirmed,
            confirmation_evidence=evidence.strip() if evidence else None,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "targetService": self.target_service,
            "interventionType": self.intervention_type.value,
            "batchIds": list(self.batch_ids),
            "interventionConfirmed": self.intervention_confirmed,
            "confirmationEvidence": self.confirmation_evidence,
        }


@dataclass(frozen=True)
class EdgeInterventionCohort:
    """A confirmed experiment that disrupts one directed service call."""

    source_service: str
    target_service: str
    intervention_type: InterventionType
    batch_ids: tuple[str, ...]
    intervention_confirmed: bool
    confirmation_evidence: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "EdgeInterventionCohort":
        try:
            intervention_type = InterventionType(data.get("interventionType"))
        except ValueError as err:
            raise ComponentDataError("interventionType is not supported") from err
        confirmed = data.get("interventionConfirmed")
        if not isinstance(confirmed, bool):
            raise ComponentDataError("interventionConfirmed must be boolean")
        evidence = data.get("confirmationEvidence")
        if confirmed and (not isinstance(evidence, str) or not evidence.strip()):
            raise ComponentDataError(
                "Confirmed edge interventions require confirmationEvidence"
            )
        if evidence is not None and not isinstance(evidence, str):
            raise ComponentDataError("confirmationEvidence must be text")
        source = _identifier(data, "sourceService")
        target = _identifier(data, "targetService")
        if source == target:
            raise ComponentDataError(
                "Edge interventions require different source and target services"
            )
        return cls(
            source_service=source,
            target_service=target,
            intervention_type=intervention_type,
            batch_ids=_batch_ids(data.get("batchIds"), "batchIds"),
            intervention_confirmed=confirmed,
            confirmation_evidence=evidence.strip() if evidence else None,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "sourceService": self.source_service,
            "targetService": self.target_service,
            "interventionType": self.intervention_type.value,
            "batchIds": list(self.batch_ids),
            "interventionConfirmed": self.intervention_confirmed,
            "confirmationEvidence": self.confirmation_evidence,
        }


@dataclass(frozen=True)
class CohortStatistics:
    total_records: int
    eligible_records: int
    excluded_records: int
    succeeded_records: int
    failed_records: int
    success_rate: float | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "totalRecords": self.total_records,
            "eligibleRecords": self.eligible_records,
            "excludedRecords": self.excluded_records,
            "succeededRecords": self.succeeded_records,
            "failedRecords": self.failed_records,
            "successRate": self.success_rate,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CohortStatistics":
        return cls(
            total_records=int(data.get("totalRecords", 0)),
            eligible_records=int(data.get("eligibleRecords", 0)),
            excluded_records=int(data.get("excludedRecords", 0)),
            succeeded_records=int(data.get("succeededRecords", 0)),
            failed_records=int(data.get("failedRecords", 0)),
            success_rate=(
                float(data["successRate"]) if data.get("successRate") is not None else None
            ),
        )


@dataclass(frozen=True)
class ServiceDependencyAssessment:
    service_name: str
    classification: ValidatedDependencyType
    baseline: CohortStatistics
    intervention: CohortStatistics
    intervention_type: InterventionType | None
    failure_effect: float | None
    evidence_strength: float
    reasons: tuple[str, ...]
    excluded_execution_ids: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "serviceName": self.service_name,
            "classification": self.classification.value,
            "baseline": self.baseline.to_mapping(),
            "intervention": self.intervention.to_mapping(),
            "interventionType": (
                self.intervention_type.value if self.intervention_type else None
            ),
            "failureEffect": self.failure_effect,
            "evidenceStrength": self.evidence_strength,
            "reasons": list(self.reasons),
            "excludedExecutionIds": list(self.excluded_execution_ids),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ServiceDependencyAssessment":
        intervention_type = data.get("interventionType")
        return cls(
            service_name=_identifier(data, "serviceName"),
            classification=ValidatedDependencyType(data.get("classification")),
            baseline=CohortStatistics.from_mapping(data.get("baseline", {})),
            intervention=CohortStatistics.from_mapping(data.get("intervention", {})),
            intervention_type=(
                InterventionType(intervention_type) if intervention_type else None
            ),
            failure_effect=(
                float(data["failureEffect"])
                if data.get("failureEffect") is not None
                else None
            ),
            evidence_strength=float(data.get("evidenceStrength", 0.0)),
            reasons=tuple(str(item) for item in data.get("reasons", [])),
            excluded_execution_ids=tuple(
                str(item) for item in data.get("excludedExecutionIds", [])
            ),
        )


@dataclass(frozen=True)
class EdgeDependencyAssessment:
    source_service: str
    target_service: str
    classification: ValidatedDependencyType
    baseline: CohortStatistics
    intervention: CohortStatistics
    intervention_type: InterventionType | None
    failure_effect: float | None
    evidence_strength: float
    reasons: tuple[str, ...]
    excluded_execution_ids: tuple[str, ...] = ()

    @property
    def edge(self) -> tuple[str, str]:
        return (self.source_service, self.target_service)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "sourceService": self.source_service,
            "targetService": self.target_service,
            "classification": self.classification.value,
            "baseline": self.baseline.to_mapping(),
            "intervention": self.intervention.to_mapping(),
            "interventionType": (
                self.intervention_type.value if self.intervention_type else None
            ),
            "failureEffect": self.failure_effect,
            "evidenceStrength": self.evidence_strength,
            "reasons": list(self.reasons),
            "excludedExecutionIds": list(self.excluded_execution_ids),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "EdgeDependencyAssessment":
        intervention_type = data.get("interventionType")
        return cls(
            source_service=_identifier(data, "sourceService"),
            target_service=_identifier(data, "targetService"),
            classification=ValidatedDependencyType(data.get("classification")),
            baseline=CohortStatistics.from_mapping(data.get("baseline", {})),
            intervention=CohortStatistics.from_mapping(data.get("intervention", {})),
            intervention_type=(
                InterventionType(intervention_type) if intervention_type else None
            ),
            failure_effect=(
                float(data["failureEffect"])
                if data.get("failureEffect") is not None
                else None
            ),
            evidence_strength=float(data.get("evidenceStrength", 0.0)),
            reasons=tuple(str(item) for item in data.get("reasons", [])),
            excluded_execution_ids=tuple(
                str(item) for item in data.get("excludedExecutionIds", [])
            ),
        )


@dataclass(frozen=True)
class DependencyAssessment:
    assessment_id: str
    workflow_id: str
    scenario_version: int
    environment_id: str
    source: str
    candidate_graph: WorkflowGraph
    structural_entry_service: str | None
    baseline_batch_ids: tuple[str, ...]
    interventions: tuple[InterventionCohort, ...]
    edge_interventions: tuple[EdgeInterventionCohort, ...]
    policy: AssessmentPolicy
    services: tuple[ServiceDependencyAssessment, ...]
    edges: tuple[EdgeDependencyAssessment, ...]
    created_at: datetime
    created_by: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "assessmentId": self.assessment_id,
            "workflowId": self.workflow_id,
            "scenarioVersion": self.scenario_version,
            "environmentId": self.environment_id,
            "source": self.source,
            "candidateGraph": self.candidate_graph.model_dump(by_alias=True),
            "structuralEntryService": self.structural_entry_service,
            "baselineBatchIds": list(self.baseline_batch_ids),
            "interventions": [item.to_mapping() for item in self.interventions],
            "edgeInterventions": [
                item.to_mapping() for item in self.edge_interventions
            ],
            "policy": self.policy.to_mapping(),
            "services": [item.to_mapping() for item in self.services],
            "edges": [item.to_mapping() for item in self.edges],
            "createdAt": _iso(self.created_at),
            "createdBy": self.created_by,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DependencyAssessment":
        graph = WorkflowGraph.model_validate(data.get("candidateGraph"))
        version = data.get("scenarioVersion")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ComponentDataError("scenarioVersion must be a positive integer")
        return cls(
            assessment_id=_identifier(data, "assessmentId"),
            workflow_id=_identifier(data, "workflowId"),
            scenario_version=version,
            environment_id=_identifier(data, "environmentId"),
            source=_required_text(data, "source"),
            candidate_graph=graph,
            structural_entry_service=(
                _identifier(data, "structuralEntryService")
                if data.get("structuralEntryService") is not None
                else None
            ),
            baseline_batch_ids=_batch_ids(
                data.get("baselineBatchIds"), "baselineBatchIds"
            ),
            interventions=tuple(
                InterventionCohort.from_mapping(item)
                for item in data.get("interventions", [])
            ),
            edge_interventions=tuple(
                EdgeInterventionCohort.from_mapping(item)
                for item in data.get("edgeInterventions", [])
            ),
            policy=AssessmentPolicy.from_mapping(data.get("policy")),
            services=tuple(
                ServiceDependencyAssessment.from_mapping(item)
                for item in data.get("services", [])
            ),
            edges=tuple(
                EdgeDependencyAssessment.from_mapping(item)
                for item in data.get("edges", [])
            ),
            created_at=_timestamp(data.get("createdAt"), "createdAt"),
            created_by=_required_text(data, "createdBy"),
        )


@dataclass(frozen=True)
class AssessmentApproval:
    assessment_id: str
    workflow_id: str
    scenario_version: int
    approved_graph: WorkflowGraph
    approved_at: datetime
    approved_by: str
    rationale: str
    excluded_supporting_services: tuple[str, ...] = field(default_factory=tuple)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "assessmentId": self.assessment_id,
            "workflowId": self.workflow_id,
            "scenarioVersion": self.scenario_version,
            "approvedGraph": self.approved_graph.model_dump(by_alias=True),
            "approvedAt": _iso(self.approved_at),
            "approvedBy": self.approved_by,
            "rationale": self.rationale,
            "excludedSupportingServices": list(self.excluded_supporting_services),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AssessmentApproval":
        version = data.get("scenarioVersion")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ComponentDataError("scenarioVersion must be a positive integer")
        return cls(
            assessment_id=_identifier(data, "assessmentId"),
            workflow_id=_identifier(data, "workflowId"),
            scenario_version=version,
            approved_graph=WorkflowGraph.model_validate(data.get("approvedGraph")),
            approved_at=_timestamp(data.get("approvedAt"), "approvedAt"),
            approved_by=_required_text(data, "approvedBy"),
            rationale=_required_text(data, "rationale"),
            excluded_supporting_services=tuple(
                str(item) for item in data.get("excludedSupportingServices", [])
            ),
        )
