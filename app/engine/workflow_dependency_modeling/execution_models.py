"""Immutable execution, correlation, assertion, and outcome records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.models import ComponentDataError


class InvocationStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class CorrelationStatus(str, Enum):
    MATCHED = "matched"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    NOT_ATTEMPTED = "not_attempted"


class ExecutionOutcomeStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INDETERMINATE = "indeterminate"


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentDataError(f"Execution registration requires non-empty '{key}'")
    return value.strip()


def _identifier(data: Mapping[str, Any], key: str) -> str:
    value = _required_text(data, key)
    if not _IDENTIFIER.fullmatch(value):
        raise ComponentDataError(f"Execution registration '{key}' has an invalid format")
    return value


def _timestamp(value: Any, key: str, *, required: bool) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ComponentDataError(f"Execution registration '{key}' must be ISO 8601")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ComponentDataError(
            f"Execution registration '{key}' must be ISO 8601"
        ) from err
    if result.tzinfo is None:
        raise ComponentDataError(
            f"Execution registration '{key}' must include a timezone"
        )
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ExecutionRegistration:
    """Caller-supplied identity and provenance for one workflow invocation."""

    execution_id: str
    batch_id: str
    workflow_id: str
    scenario_version: int
    invocation_status: InvocationStatus
    source: str
    evidence_locator: str
    environment_id: str
    started_at: datetime
    finished_at: datetime | None = None
    expected_trace_id: str | None = None
    registered_by: str = "system"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ExecutionRegistration":
        try:
            invocation_status = InvocationStatus(data.get("invocationStatus"))
        except ValueError as err:
            raise ComponentDataError(
                "invocationStatus must be completed, failed, or timed_out"
            ) from err
        version = data.get("scenarioVersion")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ComponentDataError("scenarioVersion must be a positive integer")
        expected_trace_id = data.get("expectedTraceId")
        if expected_trace_id is not None and (
            not isinstance(expected_trace_id, str) or not expected_trace_id.strip()
        ):
            raise ComponentDataError("expectedTraceId must be a non-empty string")
        if invocation_status is InvocationStatus.COMPLETED and not expected_trace_id:
            raise ComponentDataError("Completed executions require expectedTraceId")
        started_at = _timestamp(data.get("startedAt"), "startedAt", required=True)
        finished_at = _timestamp(data.get("finishedAt"), "finishedAt", required=False)
        if finished_at is not None and finished_at < started_at:
            raise ComponentDataError("finishedAt cannot precede startedAt")
        return cls(
            execution_id=_identifier(data, "executionId"),
            batch_id=_identifier(data, "batchId"),
            workflow_id=_identifier(data, "workflowId"),
            scenario_version=version,
            invocation_status=invocation_status,
            source=_required_text(data, "source"),
            evidence_locator=_required_text(data, "evidenceLocator"),
            environment_id=_identifier(
                {"environmentId": data.get("environmentId", "unspecified")},
                "environmentId",
            ),
            started_at=started_at,
            finished_at=finished_at,
            expected_trace_id=expected_trace_id.strip() if expected_trace_id else None,
            registered_by=_required_text(data, "registeredBy"),
        )

    def to_mapping(self) -> dict[str, Any]:
        result = {
            "executionId": self.execution_id,
            "batchId": self.batch_id,
            "workflowId": self.workflow_id,
            "scenarioVersion": self.scenario_version,
            "invocationStatus": self.invocation_status.value,
            "source": self.source,
            "evidenceLocator": self.evidence_locator,
            "environmentId": self.environment_id,
            "startedAt": _iso(self.started_at),
            "registeredBy": self.registered_by,
        }
        if self.finished_at is not None:
            result["finishedAt"] = _iso(self.finished_at)
        if self.expected_trace_id is not None:
            result["expectedTraceId"] = self.expected_trace_id
        return result


@dataclass(frozen=True)
class AssertionResult:
    name: str
    passed: bool
    expected: Any
    observed: Any

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class ExecutionOutcome:
    registration: ExecutionRegistration
    status: ExecutionOutcomeStatus
    correlation_status: CorrelationStatus
    recorded_at: datetime
    matched_trace_id: str | None = None
    evidence_id: str | None = None
    assertions: tuple[AssertionResult, ...] = ()
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_mapping(self) -> dict[str, Any]:
        return {
            **self.registration.to_mapping(),
            "status": self.status.value,
            "correlationStatus": self.correlation_status.value,
            "matchedTraceId": self.matched_trace_id,
            "evidenceId": self.evidence_id,
            "assertions": [item.to_mapping() for item in self.assertions],
            "reasons": list(self.reasons),
            "recordedAt": _iso(self.recorded_at),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ExecutionOutcome":
        registration = ExecutionRegistration.from_mapping(data)
        assertions = tuple(
            AssertionResult(
                name=_required_text(item, "name"),
                passed=item.get("passed") is True,
                expected=item.get("expected"),
                observed=item.get("observed"),
            )
            for item in data.get("assertions", [])
        )
        try:
            status = ExecutionOutcomeStatus(data.get("status"))
            correlation = CorrelationStatus(data.get("correlationStatus"))
        except ValueError as err:
            raise ComponentDataError("Execution outcome status is invalid") from err
        return cls(
            registration=registration,
            status=status,
            correlation_status=correlation,
            recorded_at=_timestamp(data.get("recordedAt"), "recordedAt", required=True),
            matched_trace_id=data.get("matchedTraceId"),
            evidence_id=data.get("evidenceId"),
            assertions=assertions,
            reasons=tuple(str(item) for item in data.get("reasons", [])),
        )
