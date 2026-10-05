"""Execute and gate baseline trials for controlled dependency campaigns."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
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
from app.engine.workflow_dependency_modeling.online_boutique_workload import (
    OnlineBoutiqueRunResult,
    run_online_boutique_checkout,
)
from app.engine.workflow_dependency_modeling.outcome_evaluator import evaluate_execution
from app.integrations.jaeger.client import JaegerClient


WorkloadRunner = Callable[..., OnlineBoutiqueRunResult]
TraceFetcher = Callable[[Any, str], list[dict[str, Any]]]
Sleeper = Callable[[float], None]


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
            "successCondition": {
                "protocol": contract.protocol,
                "terminalService": contract.terminal_service,
                "operationName": contract.terminal_operation,
                "httpStatusCodes": list(contract.accepted_status_codes),
                "requiredAttributes": [],
            },
            "version": 1,
            "lifecycle": "active",
            "source": "discovered",
            "createdAt": campaign.created_at.isoformat(),
            "createdBy": campaign.created_by,
            "changeSummary": "Generated from reviewed Online Boutique campaign profile",
        }
    )


def _default_trace_fetcher(analysis: Any, trace_id: str) -> list[dict[str, Any]]:
    del trace_id
    return JaegerClient(base_url=analysis.jaeger_url).fetch_traces(
        service_name=analysis.entry_service,
        operation=analysis.operation_name,
        limit=100,
        lookback="1h",
    )


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
    blockers: list[str] = []
    if len(eligible) < campaign.baseline.required_trials:
        blockers.append(
            f"Collect {campaign.baseline.required_trials - len(eligible)} more eligible baseline trial(s)."
        )
    if excluded:
        blockers.append("Resolve or restart the campaign after uncorrelated baseline trials.")
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
        "successRate": round(success_rate, 4) if success_rate is not None else None,
        "ready": not blockers,
        "blockers": blockers,
    }


def run_campaign_baseline_trial(
    campaign_id: str,
    frontend_url: str,
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
    """Run one baseline checkout and persist only evidence-honest correlation."""
    campaign = get_experiment_campaign(campaign_id, campaign_repository)
    readiness = baseline_readiness(campaign, repository)
    if readiness["ready"]:
        raise ValueError("Baseline is already ready; do not add uncontrolled samples")
    if readiness["recordedTrials"] >= campaign.baseline.required_trials * 3:
        raise ValueError("Baseline attempt limit reached; investigate evidence quality")

    analysis = get_workflow_analysis(campaign.analysis_session_id, analysis_repository)
    timestamp = _utc(now)
    resolved_runner = workload_runner or run_online_boutique_checkout
    resolved_fetcher = trace_fetcher or _default_trace_fetcher
    resolved_sleeper = sleeper or time.sleep
    run = resolved_runner(frontend_url, started_at=timestamp)
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
            "batchId": campaign.baseline.batch_id,
            "workflowId": campaign.workflow_id,
            "scenarioVersion": 1,
            "invocationStatus": "completed",
            "source": "controlled_online_boutique",
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
