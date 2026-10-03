"""Application service for registering and evaluating workflow executions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4

from app.engine.workflow_dependency_modeling.execution_models import (
    ExecutionOutcome,
    ExecutionRegistration,
    InvocationStatus,
)
from app.engine.workflow_dependency_modeling.execution_repository import (
    ExecutionRepository,
    SqlAlchemyExecutionRepository,
)
from app.engine.workflow_dependency_modeling.fixture_repository import (
    get_evidence_source,
    get_scenario,
    load_fixture_traces,
)
from app.engine.workflow_dependency_modeling.outcome_evaluator import evaluate_execution
from app.engine.workflow_dependency_modeling.scenario_service import (
    get_versioned_scenario,
)
from app.integrations.jaeger.client import JaegerClient


_default_repository: ExecutionRepository | None = None


def configure_execution_repository(repository: ExecutionRepository) -> None:
    global _default_repository
    _default_repository = repository


def _repository(repository: ExecutionRepository | None) -> ExecutionRepository:
    if repository is not None:
        return repository
    global _default_repository
    if _default_repository is None:
        from app.core.config import get_settings
        from app.core.database import get_engine

        _default_repository = SqlAlchemyExecutionRepository(
            get_engine(get_settings().database_url)
        )
    return _default_repository


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Execution timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _trace_window(trace: Mapping[str, Any], fallback: datetime) -> tuple[datetime, datetime]:
    spans = trace.get("spans", [])
    starts = [span.get("startTime") for span in spans if span.get("startTime") is not None]
    ends = [
        span.get("startTime", 0) + span.get("duration", 0)
        for span in spans
        if span.get("startTime") is not None
    ]
    if not starts or not ends:
        return fallback, fallback
    return (
        datetime.fromtimestamp(min(starts) / 1_000_000, timezone.utc),
        datetime.fromtimestamp(max(ends) / 1_000_000, timezone.utc),
    )


def _registration(
    data: Mapping[str, Any],
    actor: str,
) -> ExecutionRegistration:
    if "registeredBy" in data:
        raise ValueError("registeredBy is server-managed")
    return ExecutionRegistration.from_mapping({**data, "registeredBy": actor})


def record_execution(
    data: Mapping[str, Any],
    traces: list[dict[str, Any]],
    *,
    actor: str,
    repository: ExecutionRepository | None = None,
    now: datetime | None = None,
) -> ExecutionOutcome:
    """Evaluate and persist one externally registered execution."""
    registration = _registration(data, actor)
    scenario = get_versioned_scenario(
        registration.workflow_id,
        registration.scenario_version,
    )
    outcome = evaluate_execution(registration, scenario, traces, now=now)
    return _repository(repository).create(outcome)


def register_jaeger_execution(
    data: Mapping[str, Any],
    *,
    actor: str,
    jaeger_url: str,
    lookback: str,
    repository: ExecutionRepository | None = None,
    now: datetime | None = None,
) -> ExecutionOutcome:
    """Fetch candidate evidence from Jaeger, correlate exactly, and persist."""
    registration = _registration(data, actor)
    scenario = get_versioned_scenario(
        registration.workflow_id,
        registration.scenario_version,
    )
    traces: list[dict[str, Any]] = []
    if registration.invocation_status is InvocationStatus.COMPLETED:
        traces = JaegerClient(base_url=jaeger_url).fetch_traces(
            service_name=scenario.entry_service,
            limit=1000,
            lookback=lookback,
        )
    outcome = evaluate_execution(registration, scenario, traces, now=now)
    return _repository(repository).create(outcome)


def run_benchmark_batch(
    workflow_id: str,
    *,
    actor: str,
    batch_id: str | None = None,
    repository: ExecutionRepository | None = None,
    now: datetime | None = None,
) -> list[ExecutionOutcome]:
    """Evaluate every separated fixture trace as one auditable execution batch."""
    scenario = get_scenario(workflow_id)
    evidence = get_evidence_source(workflow_id)
    traces = load_fixture_traces(workflow_id)
    recorded_at = _utc(now)
    resolved_batch_id = batch_id or f"benchmark:{workflow_id}:{uuid4().hex[:12]}"
    outcomes: list[ExecutionOutcome] = []
    repo = _repository(repository)
    for index, trace in enumerate(traces, start=1):
        trace_id = str(trace.get("traceID", ""))
        started_at, finished_at = _trace_window(trace, recorded_at)
        registration = ExecutionRegistration.from_mapping(
            {
                "executionId": f"{resolved_batch_id}:{index}",
                "batchId": resolved_batch_id,
                "workflowId": workflow_id,
                "scenarioVersion": scenario.version,
                "invocationStatus": "completed",
                "source": "benchmark_fixture",
                "evidenceLocator": evidence.trace_file,
                "environmentId": "synthetic-fixture",
                "expectedTraceId": trace_id,
                "startedAt": _iso(started_at),
                "finishedAt": _iso(finished_at),
                "registeredBy": actor,
            }
        )
        outcome = evaluate_execution(
            registration,
            scenario,
            traces,
            now=recorded_at + timedelta(microseconds=index),
        )
        outcomes.append(repo.create(outcome))
    return outcomes


def get_execution(
    execution_id: str,
    repository: ExecutionRepository | None = None,
) -> ExecutionOutcome:
    return _repository(repository).get(execution_id)


def list_executions(
    workflow_id: str | None = None,
    batch_id: str | None = None,
    repository: ExecutionRepository | None = None,
) -> list[ExecutionOutcome]:
    return _repository(repository).list(workflow_id, batch_id)
