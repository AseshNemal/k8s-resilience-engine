"""Immutable plans for controlled workflow dependency experiments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import ComponentDataError


class CampaignStatus(str, Enum):
    BASELINE_REQUIRED = "baseline_required"
    INTERVENTIONS_REQUIRED = "interventions_required"
    READY_FOR_ASSESSMENT = "ready_for_assessment"
    REVIEWED = "reviewed"
    APPROVED = "approved"


class InterventionStatus(str, Enum):
    PLANNED = "planned"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EVIDENCE_READY = "evidence_ready"


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"Experiment campaign requires non-empty '{key}'")
    return value.strip()


def _positive_integer(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ComponentDataError(f"Experiment campaign '{key}' must be positive")
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ComponentDataError("Experiment campaign createdAt must be ISO 8601")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ComponentDataError("Experiment campaign createdAt must be ISO 8601") from err
    if result.tzinfo is None:
        raise ComponentDataError("Experiment campaign createdAt must include a timezone")
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class WorkflowSuccessContract:
    outcome: str
    protocol: str
    accepted_status_codes: tuple[int, ...]
    terminal_service: str
    terminal_operation: str
    response_marker: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "WorkflowSuccessContract":
        protocol = _text(data, "protocol").lower()
        if protocol != "http":
            raise ComponentDataError("Online Boutique campaigns currently require HTTP")
        raw_codes = data.get("acceptedStatusCodes")
        if not isinstance(raw_codes, list) or not raw_codes:
            raise ComponentDataError("Success contract requires acceptedStatusCodes")
        codes = tuple(sorted(set(raw_codes)))
        if any(isinstance(code, bool) or not isinstance(code, int) or not 100 <= code <= 599 for code in codes):
            raise ComponentDataError("acceptedStatusCodes must contain HTTP status codes")
        return cls(
            outcome=_text(data, "outcome"),
            protocol=protocol,
            accepted_status_codes=codes,
            terminal_service=_text(data, "terminalService"),
            terminal_operation=_text(data, "terminalOperation"),
            response_marker=_text(data, "responseMarker"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "protocol": self.protocol,
            "acceptedStatusCodes": list(self.accepted_status_codes),
            "terminalService": self.terminal_service,
            "terminalOperation": self.terminal_operation,
            "responseMarker": self.response_marker,
        }


@dataclass(frozen=True)
class BaselinePlan:
    batch_id: str
    required_trials: int
    maximum_failure_rate: float
    exact_trace_correlation_required: bool

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "BaselinePlan":
        maximum_failure_rate = data.get("maximumFailureRate")
        if isinstance(maximum_failure_rate, bool) or not isinstance(maximum_failure_rate, (int, float)):
            raise ComponentDataError("maximumFailureRate must be numeric")
        if not 0 <= float(maximum_failure_rate) <= 1:
            raise ComponentDataError("maximumFailureRate must be between 0 and 1")
        if data.get("exactTraceCorrelationRequired") is not True:
            raise ComponentDataError("Campaigns require exact trace correlation")
        return cls(
            batch_id=_text(data, "batchId"),
            required_trials=_positive_integer(data, "requiredTrials"),
            maximum_failure_rate=float(maximum_failure_rate),
            exact_trace_correlation_required=True,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "batchId": self.batch_id,
            "requiredTrials": self.required_trials,
            "maximumFailureRate": self.maximum_failure_rate,
            "exactTraceCorrelationRequired": self.exact_trace_correlation_required,
        }


@dataclass(frozen=True)
class InterventionPlan:
    target_service: str
    intervention_type: str
    batch_id: str
    status: InterventionStatus
    operator_approval_required: bool
    confirmation_requirements: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InterventionPlan":
        if data.get("operatorApprovalRequired") is not True:
            raise ComponentDataError("Interventions require explicit operator approval")
        requirements = tuple(str(item).strip() for item in data.get("confirmationRequirements", []))
        if not requirements or any(not item for item in requirements):
            raise ComponentDataError("Interventions require confirmation evidence")
        try:
            status = InterventionStatus(data.get("status"))
        except ValueError as err:
            raise ComponentDataError("Intervention status is invalid") from err
        return cls(
            target_service=_text(data, "targetService"),
            intervention_type=_text(data, "interventionType"),
            batch_id=_text(data, "batchId"),
            status=status,
            operator_approval_required=True,
            confirmation_requirements=requirements,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "targetService": self.target_service,
            "interventionType": self.intervention_type,
            "batchId": self.batch_id,
            "status": self.status.value,
            "operatorApprovalRequired": self.operator_approval_required,
            "confirmationRequirements": list(self.confirmation_requirements),
        }


@dataclass(frozen=True)
class ExperimentCampaign:
    campaign_id: str
    analysis_session_id: str
    environment_id: str
    workflow_id: str
    workflow_name: str
    workload_profile: str
    evidence_tier: str
    observed_trace_count: int
    candidate_services: tuple[str, ...]
    success_contract: WorkflowSuccessContract
    baseline: BaselinePlan
    interventions: tuple[InterventionPlan, ...]
    status: CampaignStatus
    blockers: tuple[str, ...]
    created_at: datetime
    created_by: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ExperimentCampaign":
        services = tuple(sorted(set(str(item).strip() for item in data.get("candidateServices", []))))
        if not services or any(not service for service in services):
            raise ComponentDataError("Campaign requires downstream candidate services")
        interventions = tuple(
            InterventionPlan.from_mapping(item) for item in data.get("interventions", [])
        )
        if tuple(item.target_service for item in interventions) != services:
            raise ComponentDataError("Campaign requires one ordered intervention per candidate service")
        try:
            status = CampaignStatus(data.get("status"))
        except ValueError as err:
            raise ComponentDataError("Campaign status is invalid") from err
        return cls(
            campaign_id=_text(data, "campaignId"),
            analysis_session_id=_text(data, "analysisSessionId"),
            environment_id=_text(data, "environmentId"),
            workflow_id=_text(data, "workflowId"),
            workflow_name=_text(data, "workflowName"),
            workload_profile=_text(data, "workloadProfile"),
            evidence_tier=_text(data, "evidenceTier"),
            observed_trace_count=_positive_integer(data, "observedTraceCount"),
            candidate_services=services,
            success_contract=WorkflowSuccessContract.from_mapping(data.get("successContract", {})),
            baseline=BaselinePlan.from_mapping(data.get("baseline", {})),
            interventions=interventions,
            status=status,
            blockers=tuple(str(item) for item in data.get("blockers", [])),
            created_at=_timestamp(data.get("createdAt")),
            created_by=_text(data, "createdBy"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "campaignId": self.campaign_id,
            "analysisSessionId": self.analysis_session_id,
            "environmentId": self.environment_id,
            "workflowId": self.workflow_id,
            "workflowName": self.workflow_name,
            "workloadProfile": self.workload_profile,
            "evidenceTier": self.evidence_tier,
            "observedTraceCount": self.observed_trace_count,
            "candidateServices": list(self.candidate_services),
            "successContract": self.success_contract.to_mapping(),
            "baseline": self.baseline.to_mapping(),
            "interventions": [item.to_mapping() for item in self.interventions],
            "status": self.status.value,
            "blockers": list(self.blockers),
            "createdAt": _iso(self.created_at),
            "createdBy": self.created_by,
        }
