"""Immutable execution, correlation, assertion, and outcome records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
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
class TraceEvidenceSnapshot:
    """Canonical, checksummed copy of the exact trace used for one decision."""

    trace_id: str
    source_locator: str
    captured_at: datetime
    sha256_digest: str
    canonical_json: str = field(repr=False)

    @classmethod
    def from_trace(
        cls,
        trace: Mapping[str, Any],
        *,
        trace_id: str,
        source_locator: str,
        captured_at: datetime,
    ) -> "TraceEvidenceSnapshot":
        if trace.get("traceID") != trace_id:
            raise ComponentDataError("Trace evidence ID does not match rawTrace.traceID")
        if captured_at.tzinfo is None:
            raise ComponentDataError("Trace evidence timestamp must include a timezone")
        canonical = json.dumps(
            trace, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        return cls(
            trace_id=trace_id,
            source_locator=source_locator,
            captured_at=captured_at,
            sha256_digest=f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}",
            canonical_json=canonical,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TraceEvidenceSnapshot":
        raw_trace = data.get("rawTrace")
        if not isinstance(raw_trace, Mapping):
            raise ComponentDataError("Trace evidence rawTrace must be an object")
        snapshot = cls.from_trace(
            raw_trace,
            trace_id=_required_text(data, "traceId"),
            source_locator=_required_text(data, "sourceLocator"),
            captured_at=_timestamp(data.get("capturedAt"), "capturedAt", required=True),
        )
        claimed = _required_text(data, "sha256")
        if claimed != snapshot.sha256_digest:
            raise ComponentDataError("Trace evidence checksum does not match rawTrace")
        return snapshot

    def to_mapping(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "sourceLocator": self.source_locator,
            "capturedAt": _iso(self.captured_at),
            "sha256": self.sha256_digest,
            "rawTrace": json.loads(self.canonical_json),
        }


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
    condition_id: str = "default"
    finished_at: datetime | None = None
    expected_trace_id: str | None = None
    registered_by: str = "system"
    experiment_record_id: str | None = None
    experiment_record_digest: str | None = None

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
        record_id = data.get("experimentRecordId")
        record_digest = data.get("experimentRecordDigest")
        if (record_id is None) != (record_digest is None):
            raise ComponentDataError("Experiment record ID and digest must be supplied together")
        if record_id is not None and (
            not isinstance(record_id, str) or not record_id.strip()
            or not isinstance(record_digest, str) or not record_digest.startswith("sha256:")
            or len(record_digest) != 71
            or any(c not in "0123456789abcdef" for c in record_digest[7:])
        ):
            raise ComponentDataError("Invalid experiment record identity or digest")
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
            condition_id=_identifier(
                {"conditionId": data.get("conditionId", "default")},
                "conditionId",
            ),
            finished_at=finished_at,
            expected_trace_id=expected_trace_id.strip() if expected_trace_id else None,
            registered_by=_required_text(data, "registeredBy"),
            experiment_record_id=record_id,
            experiment_record_digest=record_digest,
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
            "conditionId": self.condition_id,
            "startedAt": _iso(self.started_at),
            "registeredBy": self.registered_by,
        }
        if self.finished_at is not None:
            result["finishedAt"] = _iso(self.finished_at)
        if self.expected_trace_id is not None:
            result["expectedTraceId"] = self.expected_trace_id
        if self.experiment_record_id is not None:
            result["experimentRecordId"] = self.experiment_record_id
            result["experimentRecordDigest"] = self.experiment_record_digest
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
    evidence_snapshot: TraceEvidenceSnapshot | None = None
    assertions: tuple[AssertionResult, ...] = ()
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_mapping(self) -> dict[str, Any]:
        result = {
            **self.registration.to_mapping(),
            "status": self.status.value,
            "correlationStatus": self.correlation_status.value,
            "matchedTraceId": self.matched_trace_id,
            "evidenceId": self.evidence_id,
            "assertions": [item.to_mapping() for item in self.assertions],
            "reasons": list(self.reasons),
            "recordedAt": _iso(self.recorded_at),
        }
        if self.evidence_snapshot is not None:
            result["evidenceSnapshot"] = self.evidence_snapshot.to_mapping()
        return result

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
        snapshot = data.get("evidenceSnapshot")
        return cls(
            registration=registration,
            status=status,
            correlation_status=correlation,
            recorded_at=_timestamp(data.get("recordedAt"), "recordedAt", required=True),
            matched_trace_id=data.get("matchedTraceId"),
            evidence_id=data.get("evidenceId"),
            evidence_snapshot=(
                TraceEvidenceSnapshot.from_mapping(snapshot)
                if isinstance(snapshot, Mapping)
                else None
            ),
            assertions=assertions,
            reasons=tuple(str(item) for item in data.get("reasons", [])),
        )
