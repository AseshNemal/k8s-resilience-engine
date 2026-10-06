"""Execute and gate baseline trials for controlled dependency campaigns."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import time
from typing import Any
from uuid import uuid4

from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    AnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.analysis_session_service import (
    get_workflow_analysis,
)
from app.engine.workflow_dependency_modeling.execution_models import (
    CorrelationStatus,
    ExecutionOutcome,
    ExecutionOutcomeStatus,
    ExecutionRegistration,
)
from app.engine.workflow_dependency_modeling.execution_repository import (
    ExecutionRepository,
)
from app.engine.workflow_dependency_modeling.execution_service import (
    _repository as execution_repository,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_models import (
    ExperimentCampaign,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_repository import (
    ExperimentCampaignRepository,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_service import (
    get_experiment_campaign,
)
from app.engine.workflow_dependency_modeling.models import WorkflowScenario
from app.engine.workflow_dependency_modeling.outcome_evaluator import evaluate_execution
from app.engine.workflow_dependency_modeling.workflow_probe_models import (
    WorkflowProbeDefinition,
    WorkflowRunResult,
)
from app.engine.workflow_dependency_modeling.workflow_probe_runner import (
    run_http_workflow_probe,
)
from app.engine.workflow_dependency_modeling.trace_source import JaegerTraceSource
from app.engine.workflow_dependency_modeling.intervention_event_models import (
    InterventionEvent,
    InterventionEventType,
)
from app.engine.workflow_dependency_modeling.intervention_event_repository import (
    InterventionEventRepository,
    SqlAlchemyInterventionEventRepository,
)
WorkloadRunner = Callable[..., WorkflowRunResult]
TraceFetcher = Callable[[Any, str], list[dict[str, Any]]]
Sleeper = Callable[[float], None]
_default_intervention_repository: InterventionEventRepository | None = None


def configure_intervention_event_repository(
    repository: InterventionEventRepository,
) -> None:
    global _default_intervention_repository
    _default_intervention_repository = repository


def _intervention_repository(
    repository: InterventionEventRepository | None,
) -> InterventionEventRepository:
    if repository is not None:
        return repository
    global _default_intervention_repository
    if _default_intervention_repository is None:
        from app.core.config import get_settings
        from app.core.database import get_engine

        _default_intervention_repository = SqlAlchemyInterventionEventRepository(
            get_engine(get_settings().database_url)
        )
    return _default_intervention_repository


def _utc(now: datetime | None) -> datetime:
    result = now or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Campaign execution timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _scenario(campaign: ExperimentCampaign) -> WorkflowScenario:
    contract = campaign.success_contract
    return WorkflowScenario.from_mapping(
        {
            "workflowId": campaign.workflow_id,
            "description": campaign.workflow_name,
            "criticality": 1.0,
            "entryService": contract.terminal_service,
            "successCondition": contract.condition.to_mapping(),
            "version": 1,
            "lifecycle": "active",
            "source": "discovered",
            "createdAt": campaign.created_at.isoformat(),
            "createdBy": campaign.created_by,
            "changeSummary": "Generated from a reviewed workflow experiment",
        }
    )


def _default_trace_fetcher(analysis: Any, trace_id: str) -> list[dict[str, Any]]:
    del trace_id
    return JaegerTraceSource.from_configuration(
        analysis.trace_source
    ).fetch_workflow_traces(
        service_name=analysis.entry_service,
        operation=analysis.operation_name,
        limit=100,
        lookback=analysis.lookback,
    )


def _record_campaign_trial(
    campaign: ExperimentCampaign,
    analysis: Any,
    batch_id: str,
    application_url: str,
    actor: str,
    repository: ExecutionRepository | None,
    workload_runner: WorkloadRunner | None,
    trace_fetcher: TraceFetcher | None,
    sleeper: Sleeper | None,
    now: datetime | None,
) -> ExecutionOutcome:
    timestamp = _utc(now)
    definition: WorkflowProbeDefinition | None = campaign.execution_definition
    if definition is None:
        raise ValueError(
            "Campaign has no execution definition; create a new reviewed campaign"
        )
    resolved_runner = workload_runner or run_http_workflow_probe
    resolved_fetcher = trace_fetcher or _default_trace_fetcher
    resolved_sleeper = sleeper or time.sleep
    run = resolved_runner(application_url, definition, started_at=timestamp)
    traces: list[dict[str, Any]] = []
    for attempt in range(5):
        traces = resolved_fetcher(analysis, run.trace_id)
        if any(trace.get("traceID") == run.trace_id for trace in traces):
            break
        if attempt < 4:
            resolved_sleeper(0.4)

    registration = ExecutionRegistration.from_mapping(
        {
            "executionId": f"execution:{campaign.campaign_id.split(':')[-1]}:{uuid4().hex[:12]}",
            "batchId": batch_id,
            "workflowId": campaign.workflow_id,
            "scenarioVersion": 1,
            "invocationStatus": "completed",
            "source": "controlled_workflow_experiment",
            "evidenceLocator": f"{analysis.jaeger_url}/api/traces/{run.trace_id}",
            "environmentId": campaign.environment_id,
            "expectedTraceId": run.trace_id,
            "startedAt": run.started_at.isoformat(),
            "finishedAt": run.finished_at.isoformat(),
            "registeredBy": actor,
        }
    )
    outcome = evaluate_execution(registration, _scenario(campaign), traces, now=timestamp)
    if (
        not run.business_success
        and outcome.correlation_status is CorrelationStatus.MATCHED
        and outcome.status is ExecutionOutcomeStatus.SUCCEEDED
    ):
        outcome = ExecutionOutcome(
            registration=outcome.registration,
            status=ExecutionOutcomeStatus.FAILED,
            correlation_status=outcome.correlation_status,
            recorded_at=outcome.recorded_at,
            matched_trace_id=outcome.matched_trace_id,
            evidence_id=outcome.evidence_id,
            assertions=outcome.assertions,
            reasons=(*outcome.reasons, run.reason),
        )
    return execution_repository(repository).create(outcome)


def baseline_readiness(
    campaign: ExperimentCampaign,
    repository: ExecutionRepository | None = None,
) -> dict[str, Any]:
    records = execution_repository(repository).list(batch_id=campaign.baseline.batch_id)
    eligible = [
        item
        for item in records
        if item.correlation_status is CorrelationStatus.MATCHED
        and item.status in {ExecutionOutcomeStatus.SUCCEEDED, ExecutionOutcomeStatus.FAILED}
    ]
    successes = sum(item.status is ExecutionOutcomeStatus.SUCCEEDED for item in eligible)
    failures = sum(item.status is ExecutionOutcomeStatus.FAILED for item in eligible)
    excluded = len(records) - len(eligible)
    success_rate = successes / len(eligible) if eligible else None
    trace_ids = [item.matched_trace_id for item in eligible]
    duplicate_trace_ids = sorted(
        {trace_id for trace_id in trace_ids if trace_id and trace_ids.count(trace_id) > 1}
    )
    blockers: list[str] = []
    if len(eligible) < campaign.baseline.required_trials:
        blockers.append(
            f"Collect {campaign.baseline.required_trials - len(eligible)} more eligible baseline trial(s)."
        )
    if excluded:
        blockers.append("Resolve or restart the campaign after uncorrelated baseline trials.")
    if duplicate_trace_ids:
        blockers.append("Baseline contains a trace reused by multiple trial records.")
    if success_rate is not None and 1 - success_rate > campaign.baseline.maximum_failure_rate:
        blockers.append("Baseline failure rate exceeds the campaign stability gate.")
    return {
        "campaignId": campaign.campaign_id,
        "batchId": campaign.baseline.batch_id,
        "requiredTrials": campaign.baseline.required_trials,
        "recordedTrials": len(records),
        "eligibleTrials": len(eligible),
        "successfulTrials": successes,
        "failedTrials": failures,
        "excludedTrials": excluded,
        "duplicateTraceIds": duplicate_trace_ids,
        "successRate": round(success_rate, 4) if success_rate is not None else None,
        "ready": not blockers,
        "blockers": blockers,
    }


def run_campaign_baseline_trial(
    campaign_id: str,
    application_url: str,
    *,
    actor: str,
    campaign_repository: ExperimentCampaignRepository | None = None,
    analysis_repository: AnalysisSessionRepository | None = None,
    repository: ExecutionRepository | None = None,
    workload_runner: WorkloadRunner | None = None,
    trace_fetcher: TraceFetcher | None = None,
    sleeper: Sleeper | None = None,
    now: datetime | None = None,
) -> ExecutionOutcome:
    """Run one baseline workflow and persist only evidence-honest correlation."""
    campaign = get_experiment_campaign(campaign_id, campaign_repository)
    readiness = baseline_readiness(campaign, repository)
    if readiness["ready"]:
        raise ValueError("Baseline is already ready; do not add uncontrolled samples")
    if readiness["recordedTrials"] >= campaign.baseline.required_trials * 3:
        raise ValueError("Baseline attempt limit reached; investigate evidence quality")

    analysis = get_workflow_analysis(campaign.analysis_session_id, analysis_repository)
    return _record_campaign_trial(
        campaign,
        analysis,
        campaign.baseline.batch_id,
        application_url,
        actor,
        repository,
        workload_runner,
        trace_fetcher,
        sleeper,
        now,
    )


def run_campaign_intervention_trial(
    campaign_id: str,
    target_service: str,
    application_url: str,
    *,
    actor: str,
    campaign_repository: ExperimentCampaignRepository | None = None,
    analysis_repository: AnalysisSessionRepository | None = None,
    event_repository: InterventionEventRepository | None = None,
    repository: ExecutionRepository | None = None,
    workload_runner: WorkloadRunner | None = None,
    trace_fetcher: TraceFetcher | None = None,
    sleeper: Sleeper | None = None,
    now: datetime | None = None,
) -> ExecutionOutcome:
    """Run one workflow only while the selected intervention is confirmed active."""
    campaign = get_experiment_campaign(campaign_id, campaign_repository)
    if not baseline_readiness(campaign, repository)["ready"]:
        raise ValueError("Baseline readiness must pass before intervention trials")
    plan = next(
        (item for item in campaign.interventions if item.target_service == target_service),
        None,
    )
    if plan is None:
        raise ValueError("targetService is not a candidate in this campaign")
    events = _intervention_repository(event_repository).list(
        campaign.campaign_id, target_service
    )
    if not events or events[-1].event_type is not InterventionEventType.ACTIVATED:
        raise ValueError("Intervention must be confirmed active before collecting trials")
    attempts = execution_repository(repository).list(batch_id=plan.batch_id)
    if len(attempts) >= campaign.baseline.required_trials * 3:
        raise ValueError("Intervention attempt limit reached; investigate evidence quality")
    analysis = get_workflow_analysis(campaign.analysis_session_id, analysis_repository)
    return _record_campaign_trial(
        campaign,
        analysis,
        plan.batch_id,
        application_url,
        actor,
        repository,
        workload_runner,
        trace_fetcher,
        sleeper,
        now,
    )


def record_intervention_event(
    campaign_id: str,
    data: dict[str, Any],
    *,
    actor: str,
    campaign_repository: ExperimentCampaignRepository | None = None,
    repository: InterventionEventRepository | None = None,
    execution_repository_override: ExecutionRepository | None = None,
    now: datetime | None = None,
) -> InterventionEvent:
    """Record operator-confirmed activation or restoration without applying a fault."""
    forbidden = {
        "eventId",
        "campaignId",
        "batchId",
        "recordedAt",
        "recordedBy",
    } & data.keys()
    if forbidden:
        raise ValueError(
            f"Server-managed fields are not accepted: {', '.join(sorted(forbidden))}"
        )
    if data.get("operatorConfirmed") is not True:
        raise ValueError("operatorConfirmed must be true for intervention evidence")
    campaign = get_experiment_campaign(campaign_id, campaign_repository)
    target_service = data.get("targetService")
    plan = next(
        (item for item in campaign.interventions if item.target_service == target_service),
        None,
    )
    if plan is None:
        raise ValueError("targetService is not a candidate in this campaign")
    try:
        event_type = InterventionEventType(data.get("eventType"))
    except ValueError as err:
        raise ValueError("eventType must be activated or restored") from err

    event_repository = _intervention_repository(repository)
    existing = event_repository.list(campaign_id, plan.target_service)
    active = bool(existing and existing[-1].event_type is InterventionEventType.ACTIVATED)
    if event_type is InterventionEventType.ACTIVATED:
        if active:
            raise ValueError("This service already has an active intervention")
        if not baseline_readiness(campaign, execution_repository_override)["ready"]:
            raise ValueError("Baseline readiness must pass before an intervention is activated")
    elif not active:
        raise ValueError("A restoration requires an active intervention")

    recorded_at = _utc(now)
    event = InterventionEvent.from_mapping(
        {
            "eventId": f"intervention-event:{uuid4().hex}",
            "campaignId": campaign.campaign_id,
            "targetService": plan.target_service,
            "batchId": plan.batch_id,
            "eventType": event_type.value,
            "evidence": data.get("evidence"),
            "occurredAt": data.get("occurredAt"),
            "recordedAt": recorded_at.isoformat(),
            "recordedBy": actor,
        }
    )
    if event.occurred_at > recorded_at + timedelta(minutes=5):
        raise ValueError("occurredAt cannot be more than five minutes in the future")
    if existing and event.occurred_at < existing[-1].occurred_at:
        raise ValueError("Intervention events must be recorded in chronological order")
    return event_repository.create(event)


def intervention_status(
    campaign_id: str,
    *,
    campaign_repository: ExperimentCampaignRepository | None = None,
    repository: InterventionEventRepository | None = None,
    execution_repository_override: ExecutionRepository | None = None,
) -> dict[str, Any]:
    campaign = get_experiment_campaign(campaign_id, campaign_repository)
    event_repository = _intervention_repository(repository)
    services = []
    for plan in campaign.interventions:
        events = event_repository.list(campaign_id, plan.target_service)
        state = "planned"
        if events:
            state = (
                "active"
                if events[-1].event_type is InterventionEventType.ACTIVATED
                else "restored"
            )
        records = execution_repository(execution_repository_override).list(
            batch_id=plan.batch_id
        )
        trial_count = len(records)
        eligible = [
            item
            for item in records
            if item.correlation_status is CorrelationStatus.MATCHED
            and item.status
            in {ExecutionOutcomeStatus.SUCCEEDED, ExecutionOutcomeStatus.FAILED}
        ]
        trace_ids = [item.matched_trace_id for item in eligible]
        duplicate_trace_ids = sorted(
            {trace_id for trace_id in trace_ids if trace_id and trace_ids.count(trace_id) > 1}
        )
        excluded = len(records) - len(eligible)
        services.append(
            {
                "targetService": plan.target_service,
                "batchId": plan.batch_id,
                "state": state,
                "trialCount": trial_count,
                "eligibleTrials": len(eligible),
                "requiredTrials": campaign.baseline.required_trials,
                "excludedTrials": excluded,
                "duplicateTraceIds": duplicate_trace_ids,
                "evidenceReady": (
                    state == "restored"
                    and len(eligible) >= campaign.baseline.required_trials
                    and excluded == 0
                    and not duplicate_trace_ids
                ),
                "events": [event.to_mapping() for event in events],
            }
        )
    return {
        "campaignId": campaign.campaign_id,
        "baselineReady": baseline_readiness(
            campaign, execution_repository_override
        )["ready"],
        "services": services,
    }
