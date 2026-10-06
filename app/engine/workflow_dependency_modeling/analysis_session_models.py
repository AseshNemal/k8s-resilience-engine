"""Guided operator-session models with explicit evidence maturity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.engine.workflow_dependency_modeling.trace_source import (
    TraceSourceConfiguration,
)


class EvidenceTier(str, Enum):
    EXPERIMENTAL = "experimental"
    PRODUCTION = "production"


class AnalysisMaturity(str, Enum):
    OBSERVED = "observed"
    CANDIDATE = "candidate"
    EXPERIMENT_INCOMPLETE = "experiment_incomplete"
    VALIDATED = "validated"
    APPROVED = "approved"


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"Analysis session requires non-empty '{key}'")
    return value.strip()


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ComponentDataError("Analysis session createdAt must be ISO 8601")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ComponentDataError("Analysis session createdAt must be ISO 8601") from err
    if result.tzinfo is None:
        raise ComponentDataError("Analysis session createdAt must include a timezone")
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class WorkflowAnalysisSession:
    session_id: str
    environment_id: str
    evidence_tier: EvidenceTier
    trace_source: TraceSourceConfiguration
    entry_service: str
    operation_name: str
    lookback: str
    workflow_id: str
    trace_count: int
    observed_services: tuple[str, ...]
    service_evidence: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, str], ...]
    maturity: AnalysisMaturity
    next_action: str
    limitations: tuple[str, ...]
    required_actions: tuple[str, ...]
    created_at: datetime
    created_by: str

    @property
    def jaeger_url(self) -> str:
        """Compatibility accessor for existing orchestration code."""
        return self.trace_source.endpoint

    def to_mapping(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "environmentId": self.environment_id,
            "evidenceTier": self.evidence_tier.value,
            "traceSource": self.trace_source.to_mapping(),
            "jaegerUrl": self.jaeger_url,
            "entryService": self.entry_service,
            "operationName": self.operation_name,
            "lookback": self.lookback,
            "workflowId": self.workflow_id,
            "traceCount": self.trace_count,
            "observedServices": list(self.observed_services),
            "serviceEvidence": list(self.service_evidence),
            "edges": list(self.edges),
            "maturity": self.maturity.value,
            "nextAction": self.next_action,
            "limitations": list(self.limitations),
            "requiredActions": list(self.required_actions),
            "createdAt": _iso(self.created_at),
            "createdBy": self.created_by,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "WorkflowAnalysisSession":
        trace_count = data.get("traceCount")
        if isinstance(trace_count, bool) or not isinstance(trace_count, int) or trace_count < 1:
            raise ComponentDataError("Analysis session traceCount must be positive")
        try:
            tier = EvidenceTier(data.get("evidenceTier"))
            maturity = AnalysisMaturity(data.get("maturity"))
        except ValueError as err:
            raise ComponentDataError("Analysis session evidence state is invalid") from err
        services = tuple(str(item).strip() for item in data.get("observedServices", []))
        if not services or any(not item for item in services):
            raise ComponentDataError("Analysis session requires observed services")
        trace_source_data = data.get("traceSource")
        if trace_source_data is None:
            trace_source_data = {
                "type": "jaeger",
                "endpoint": _text(data, "jaegerUrl"),
                "authentication": {"type": "none"},
            }
        return cls(
            session_id=_text(data, "sessionId"),
            environment_id=_text(data, "environmentId"),
            evidence_tier=tier,
            trace_source=TraceSourceConfiguration.from_mapping(trace_source_data),
            entry_service=_text(data, "entryService"),
            operation_name=_text(data, "operationName"),
            lookback=_text(data, "lookback"),
            workflow_id=_text(data, "workflowId"),
            trace_count=trace_count,
            observed_services=services,
            service_evidence=tuple(dict(item) for item in data.get("serviceEvidence", [])),
            edges=tuple(dict(item) for item in data.get("edges", [])),
            maturity=maturity,
            next_action=_text(data, "nextAction"),
            limitations=tuple(str(item) for item in data.get("limitations", [])),
            required_actions=tuple(str(item) for item in data.get("requiredActions", [])),
            created_at=_timestamp(data.get("createdAt")),
            created_by=_text(data, "createdBy"),
        )
