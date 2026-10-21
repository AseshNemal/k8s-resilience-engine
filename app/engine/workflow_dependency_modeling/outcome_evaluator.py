"""Deterministic trace correlation and workflow-success assertion evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.engine.workflow_dependency_modeling.execution_models import (
    AssertionResult,
    CorrelationStatus,
    ExecutionOutcome,
    ExecutionOutcomeStatus,
    ExecutionRegistration,
    InvocationStatus,
    TraceEvidenceSnapshot,
)
from app.engine.workflow_dependency_modeling.auto_discovery import operation_key
from app.engine.workflow_dependency_modeling.models import (
    SuccessCondition,
    SuccessProtocol,
    WorkflowScenario,
)
from app.engine.workflow_dependency_modeling.trace_reader import Span, parse_spans


_GRPC_CODES = {
    "0": "OK",
    "1": "CANCELLED",
    "2": "UNKNOWN",
    "3": "INVALID_ARGUMENT",
    "4": "DEADLINE_EXCEEDED",
    "5": "NOT_FOUND",
    "6": "ALREADY_EXISTS",
    "7": "PERMISSION_DENIED",
    "8": "RESOURCE_EXHAUSTED",
    "9": "FAILED_PRECONDITION",
    "10": "ABORTED",
    "11": "OUT_OF_RANGE",
    "12": "UNIMPLEMENTED",
    "13": "INTERNAL",
    "14": "UNAVAILABLE",
    "15": "DATA_LOSS",
    "16": "UNAUTHENTICATED",
}
_SPAN_CODES = {"0": "UNSET", "1": "OK", "2": "ERROR"}


def _recorded_at(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Outcome timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _outcome_without_trace(
    registration: ExecutionRegistration,
    status: ExecutionOutcomeStatus,
    correlation: CorrelationStatus,
    reason: str,
    now: datetime | None,
) -> ExecutionOutcome:
    return ExecutionOutcome(
        registration=registration,
        status=status,
        correlation_status=correlation,
        recorded_at=_recorded_at(now),
        reasons=(reason,),
    )


def _tag(span: Span, *names: str) -> str | None:
    return next((span.tags[name] for name in names if name in span.tags), None)


def _protocol_assertion(
    condition: SuccessCondition,
    terminal: Span,
) -> AssertionResult:
    if condition.protocol is SuccessProtocol.HTTP:
        observed = _tag(terminal, "http.response.status_code", "http.status_code")
        expected: list[Any] = list(condition.http_status_codes)
        passed = observed is not None and observed.isdigit() and int(observed) in expected
    elif condition.protocol is SuccessProtocol.GRPC:
        raw = _tag(terminal, "rpc.grpc.status_code", "grpc.status_code")
        observed = _GRPC_CODES.get(raw or "", (raw or "").upper() or None)
        expected = list(condition.grpc_status_codes)
        passed = observed in expected
    else:
        raw = _tag(terminal, "otel.status_code", "status.code")
        observed = _SPAN_CODES.get(raw or "", (raw or "").upper() or None)
        if observed is None:
            observed = "ERROR" if terminal.error else "UNSET"
        expected = list(condition.span_status_codes)
        passed = observed in expected
    return AssertionResult("protocol_status", passed, expected, observed)


def _terminal_assertions(
    condition: SuccessCondition,
    terminal: Span,
) -> tuple[AssertionResult, ...]:
    assertions = [_protocol_assertion(condition, terminal)]
    if condition.max_duration_ms is not None:
        observed_duration = terminal.duration_us / 1000
        assertions.append(
            AssertionResult(
                "terminal_duration_ms",
                observed_duration <= condition.max_duration_ms,
                {"maximum": condition.max_duration_ms},
                observed_duration,
            )
        )
    assertions.extend(
        AssertionResult(
            f"attribute:{required.key}",
            terminal.tags.get(required.key) == required.value,
            required.value,
            terminal.tags.get(required.key),
        )
        for required in condition.required_attributes
    )
    return tuple(assertions)


def _matches_terminal(condition: SuccessCondition, span: Span) -> bool:
    return span.service_name == condition.terminal_service and (
        span.operation_name == condition.operation_name
        or (
            condition.protocol is SuccessProtocol.HTTP
            and operation_key(span) == condition.operation_name
        )
    )


def trace_has_terminal_evidence(
    trace: dict[str, Any],
    condition: SuccessCondition,
) -> bool:
    """Return whether an incrementally exported trace includes its terminal span."""
    return any(_matches_terminal(condition, span) for span in parse_spans([trace]))


def evaluate_trace_success(
    condition: SuccessCondition, trace: dict[str, Any],
) -> tuple[ExecutionOutcomeStatus, tuple[AssertionResult, ...], tuple[str, ...]]:
    """Evaluate trace assertions without claiming an independently registered run.

    Callers must quality-check the trace before parsing it. Missing or ambiguous
    terminal evidence stays indeterminate rather than becoming a failed run.
    """
    terminals = [span for span in parse_spans([trace]) if _matches_terminal(condition, span)]
    if len(terminals) != 1:
        reason = (
            "Terminal service and operation were not observed; missing telemetry alone does not prove workflow failure"
            if not terminals else "Multiple terminal spans matched the success condition"
        )
        return ExecutionOutcomeStatus.INDETERMINATE, (), (reason,)
    if condition.max_duration_ms is not None and any(
        span.get("spanID") == terminals[0].span_id and "duration" not in span
        for span in trace.get("spans", [])
    ):
        return ExecutionOutcomeStatus.INDETERMINATE, (), ("Terminal duration is missing; the duration assertion cannot be evaluated",)
    assertions = _terminal_assertions(condition, terminals[0])
    passed = all(item.passed for item in assertions)
    return (
        ExecutionOutcomeStatus.SUCCEEDED if passed else ExecutionOutcomeStatus.FAILED,
        assertions,
        () if passed else ("One or more success assertions failed",),
    )


def _matched_trace_outcome(
    registration: ExecutionRegistration,
    scenario: WorkflowScenario,
    trace: dict[str, Any],
    now: datetime | None,
) -> ExecutionOutcome:
    outcome_status, assertions, reasons = evaluate_trace_success(scenario.success_condition, trace)
    evidence_id = f"trace:{registration.expected_trace_id}"
    recorded_at = _recorded_at(now)
    snapshot = TraceEvidenceSnapshot.from_trace(
        trace,
        trace_id=str(registration.expected_trace_id),
        source_locator=registration.evidence_locator,
        captured_at=recorded_at,
    )
    return ExecutionOutcome(
        registration=registration,
        status=outcome_status,
        correlation_status=CorrelationStatus.MATCHED,
        recorded_at=recorded_at,
        matched_trace_id=registration.expected_trace_id,
        evidence_id=evidence_id,
        evidence_snapshot=snapshot,
        assertions=assertions,
        reasons=reasons,
    )


def evaluate_execution(
    registration: ExecutionRegistration,
    scenario: WorkflowScenario,
    traces: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> ExecutionOutcome:
    """Correlate exactly one trace and evaluate the scenario's success contract."""
    if registration.workflow_id != scenario.workflow_id:
        raise ValueError("Execution workflow does not match the scenario")
    if registration.scenario_version != scenario.version:
        raise ValueError("Execution scenario version does not match the scenario")
    if registration.invocation_status is InvocationStatus.FAILED:
        return _outcome_without_trace(
            registration,
            ExecutionOutcomeStatus.FAILED,
            CorrelationStatus.NOT_ATTEMPTED,
            "Workflow invocation failed before trace assertion evaluation",
            now,
        )
    if registration.invocation_status is InvocationStatus.TIMED_OUT:
        return _outcome_without_trace(
            registration,
            ExecutionOutcomeStatus.TIMED_OUT,
            CorrelationStatus.NOT_ATTEMPTED,
            "Workflow invocation timed out",
            now,
        )
    matches = [
        trace
        for trace in traces
        if trace.get("traceID") == registration.expected_trace_id
    ]
    if not matches:
        return _outcome_without_trace(
            registration,
            ExecutionOutcomeStatus.INDETERMINATE,
            CorrelationStatus.MISSING,
            "No trace matched the registered trace ID",
            now,
        )
    if len(matches) > 1:
        return _outcome_without_trace(
            registration,
            ExecutionOutcomeStatus.INDETERMINATE,
            CorrelationStatus.AMBIGUOUS,
            "Multiple traces matched the registered trace ID",
            now,
        )
    return _matched_trace_outcome(registration, scenario, matches[0], now)
