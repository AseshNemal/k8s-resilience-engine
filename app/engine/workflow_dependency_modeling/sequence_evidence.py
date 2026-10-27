"""Capture action-level traces without inventing links between HTTP requests."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from .execution_models import (
    CorrelationStatus, ExecutionOutcome, ExecutionOutcomeStatus,
    TraceEvidenceSnapshot, WorkflowStepEvidence,
)
from .outcome_evaluator import trace_has_terminal_evidence
from .trace_validator import validate_trace_quality, expected_outage_trace_quality
from .workflow_probe_models import WorkflowProbeDefinition, WorkflowRunResult


def trace_locator(analysis: Any, trace_id: str) -> str:
    path = {"jaeger": "/api/traces/", "zipkin": "/api/v2/trace/", "tempo": "/api/v2/traces/"}
    return analysis.trace_source.endpoint.rstrip("/") + path[analysis.trace_source.kind.value] + trace_id


def collect_run_traces(
    run: WorkflowRunResult, definition: WorkflowProbeDefinition, analysis: Any,
    fetcher: Callable, sleeper: Callable, delays: tuple[float, ...],
    outage_target: str | None,
) -> dict[str, list[dict[str, Any]]]:
    """Poll pending action traces within one shared collection budget.

    Historical benchmark adapters expose only a selected trace ID. They retain
    that behavior and are never upgraded to sequence-complete evidence.
    """
    steps = getattr(run, "steps", ())
    ids = [item.trace_id for item in steps if item.trace_id]
    if len(ids) != len(set(ids)):
        raise ValueError("Workflow steps must use distinct trace IDs")
    if steps and len(ids) != len(steps):
        raise ValueError("Every attempted workflow step must retain its trace ID")
    pending = set(ids) | {run.trace_id}
    collected: dict[str, list[dict[str, Any]]] = {}
    for attempt in range(len(delays) + 1):
        for trace_id in sorted(pending):
            matches = [t for t in fetcher(analysis, trace_id) if t.get("traceID") == trace_id]
            collected[trace_id] = matches
            if len(matches) != 1:
                continue
            trace = matches[0]
            quality_ok = (
                validate_trace_quality(matches).valid_count == 1
                or expected_outage_trace_quality(trace, outage_target)
            )
            terminal_ok = (
                trace_id != run.trace_id or not run.business_success
                or trace_has_terminal_evidence(trace, definition.success_condition)
            )
            if quality_ok and terminal_ok:
                pending.remove(trace_id)
        if not pending:
            break
        if attempt < len(delays):
            sleeper(delays[attempt])
    return collected


def capture_step_evidence(
    run: WorkflowRunResult, definition: WorkflowProbeDefinition, analysis: Any,
    collected: dict[str, list[dict[str, Any]]], captured_at: datetime,
) -> tuple[WorkflowStepEvidence, ...] | None:
    attempted = getattr(run, "steps", ())
    if not attempted:
        return None
    if [step.name for step in attempted] != [step.name for step in definition.steps[:len(attempted)]]:
        raise ValueError("Attempted steps do not match the reviewed workflow sequence")
    by_name = {step.name: step for step in attempted}
    evidence = []
    for step in definition.steps:
        result = by_name.get(step.name)
        if result is None:
            evidence.append(WorkflowStepEvidence(step.name, step.method, step.path, False))
            continue
        matches = collected.get(result.trace_id, [])
        correlation = (
            CorrelationStatus.MATCHED if len(matches) == 1
            else CorrelationStatus.AMBIGUOUS if matches else CorrelationStatus.MISSING
        )
        snapshot = TraceEvidenceSnapshot.from_trace(
            matches[0], trace_id=result.trace_id,
            source_locator=trace_locator(analysis, result.trace_id), captured_at=captured_at,
        ) if len(matches) == 1 else None
        evidence.append(WorkflowStepEvidence(
            name=step.name, method=step.method, path=step.path, attempted=True,
            trace_id=result.trace_id, started_at=result.started_at, finished_at=result.finished_at,
            status_code=result.status_code, succeeded=result.succeeded, reason=result.reason,
            correlation_status=correlation, evidence_snapshot=snapshot,
            assertions=result.assertions,
        ))
    return tuple(evidence)


def _step_blockers(step: Any, outage_target: str | None) -> list[str]:
    blockers = []
    snapshot = step.evidence_snapshot
    if step.assertions and step.succeeded != all(item.passed for item in step.assertions):
        blockers.append(f"Step '{step.name}' contradicts its recorded assertions")
    if step.correlation_status is not CorrelationStatus.MATCHED or snapshot is None:
        blockers.append(f"Step '{step.name}' has no unique matching trace")
    elif (
        validate_trace_quality([snapshot.to_mapping()["rawTrace"]]).valid_count != 1
        and not expected_outage_trace_quality(snapshot.to_mapping()["rawTrace"], outage_target)
    ):
        blockers.append(f"Step '{step.name}' has incomplete or invalid trace evidence")
    return blockers


def sequence_evidence_blockers(
    outcome: ExecutionOutcome, outage_target: str | None = None,
) -> list[str]:
    """Legacy absence stays explicit; captured sequences must pass every action gate."""
    if outcome.step_evidence is None:
        return []
    blockers = []
    attempted = [step for step in outcome.step_evidence if step.attempted]
    if not attempted:
        return ["Workflow sequence has no attempted actions"]
    if outcome.status is ExecutionOutcomeStatus.SUCCEEDED and any(
        not step.attempted or step.succeeded is not True for step in outcome.step_evidence
    ):
        blockers.append("Successful workflow lacks successful evidence for every reviewed action")
    ids = [step.trace_id for step in attempted]
    if len(ids) != len(set(ids)):
        blockers.append("Workflow actions reuse a trace ID")
    if outcome.matched_trace_id not in ids:
        blockers.append("Selected outcome trace is absent from the workflow sequence")
    for step in attempted:
        blockers.extend(_step_blockers(step, outage_target))
    return blockers
