"""Execute and gate baseline trials for controlled dependency campaigns."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
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
    AssertionResult,
    CorrelationStatus,
    ExecutionOutcome,
    ExecutionOutcomeStatus,
    ExecutionRegistration,
    TraceEvidenceSnapshot,
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
from app.engine.workflow_dependency_modeling.outcome_evaluator import (
    evaluate_execution,
    trace_has_terminal_evidence,
)
from app.engine.workflow_dependency_modeling.workflow_probe_models import (
    WorkflowProbeDefinition,
    WorkflowRunResult,
)
from app.engine.workflow_dependency_modeling.workflow_probe_runner import (
    run_http_workflow_probe,
)
from app.engine.workflow_dependency_modeling.trace_source import create_trace_source
from app.engine.workflow_dependency_modeling.trace_validator import (
    TraceQuality, validate_trace_quality, expected_outage_trace_quality,
)
from app.engine.workflow_dependency_modeling.trace_reader import parse_spans
from .sequence_evidence import (
    collect_run_traces, capture_step_evidence, sequence_evidence_blockers,
)
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
TRACE_POLL_DELAYS_SECONDS = (0.25, 0.5, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0)
_VERIFIED_OUTAGE_ASSERTION = "verified_unavailable_service:"
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
            "criticality": campaign.require_criticality(),
            "entryService": campaign.entry_service,
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
    return create_trace_source(analysis.trace_source).fetch_trace(trace_id)


def controlled_outage_trace_ids(repository: ExecutionRepository | None = None) -> frozenset[str]:
    """Trace IDs recorded by outage runs (service cohorts and held-out pairs), preparation steps included.

    Their missing spans were caused on purpose. Later inspections set them aside, so an experiment's own
    outages are never mistaken for broken telemetry of the application.
    """
    ids: set[str] = set()
    for outcome in execution_repository(repository).list():
        batch = outcome.registration.batch_id
        verified = any(item.name.startswith(_VERIFIED_OUTAGE_ASSERTION) for item in outcome.assertions)
        if verified or ":intervention:" in batch or ":heldout:" in batch:
            ids.update(outcome.trace_ids)
    return frozenset(ids)


def _outage_label(target: str | tuple[str, ...]) -> str:
    """One verified service, or several taken down together joined with '+' (held-out scenarios)."""
    return target if isinstance(target, str) else "+".join(sorted(target))


def _expected_outage_trace_quality(trace: dict[str, Any], target: str | tuple[str, ...] | None) -> bool:
    """Tolerate only the missing server span caused by one verified outage."""
    return expected_outage_trace_quality(trace, target)


def _verified_outage_target(outcome: ExecutionOutcome) -> str | None:
    for assertion in outcome.assertions:
        if assertion.name.startswith(_VERIFIED_OUTAGE_ASSERTION) and assertion.passed:
            target = assertion.name.removeprefix(_VERIFIED_OUTAGE_ASSERTION)
            if (
                assertion.expected == target
                and assertion.observed == target
                and outcome.registration.batch_id.endswith(f":intervention:{target}")
            ):
                return target
    return None


def _outcome_from_traces(
    registration: ExecutionRegistration,
    scenario: Any,
    run: Any,
    traces: list[dict[str, Any]],
    recorded_at: datetime,
    verified_unavailable_service: str | tuple[str, ...] | None,
) -> ExecutionOutcome:
    """Evaluate the run's own trace; poor telemetry stays indeterminate, a verified outage is recorded as an assertion."""
    matching_traces = [trace for trace in traces if trace.get("traceID") == run.trace_id]
    if len(matching_traces) != 1:
        return evaluate_execution(registration, scenario, traces, now=recorded_at)
    quality = validate_trace_quality(matching_traces).results[0]
    expected_outage = _expected_outage_trace_quality(matching_traces[0], verified_unavailable_service)
    if not quality.eligible and not expected_outage:
        snapshot = TraceEvidenceSnapshot.from_trace(
            matching_traces[0],
            trace_id=run.trace_id,
            source_locator=registration.evidence_locator,
            captured_at=recorded_at,
        )
        return ExecutionOutcome(
            registration=registration,
            status=ExecutionOutcomeStatus.INDETERMINATE,
            correlation_status=CorrelationStatus.MATCHED,
            recorded_at=recorded_at,
            matched_trace_id=run.trace_id,
            evidence_id=f"trace:{run.trace_id}",
            evidence_snapshot=snapshot,
            reasons=(
                "Trace quality is " + quality.quality.value
                + "; incomplete telemetry cannot establish a workflow outcome",
                *quality.reasons,
            ),
        )
    outcome = evaluate_execution(
        registration, scenario, traces, now=recorded_at,
        verified_unavailable_service=verified_unavailable_service,
    )
    if verified_unavailable_service is None:
        return outcome
    label = _outage_label(verified_unavailable_service)
    return ExecutionOutcome(
        registration=outcome.registration,
        status=outcome.status,
        correlation_status=outcome.correlation_status,
        recorded_at=outcome.recorded_at,
        matched_trace_id=outcome.matched_trace_id,
        evidence_id=outcome.evidence_id,
        evidence_snapshot=outcome.evidence_snapshot,
        assertions=(
            *outcome.assertions,
            AssertionResult(
                name=_VERIFIED_OUTAGE_ASSERTION + label, passed=True, expected=label, observed=label,
            ),
        ),
        reasons=outcome.reasons,
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
    verified_unavailable_service: str | tuple[str, ...] | None = None,
) -> ExecutionOutcome:
    timestamp = _utc(now)
    from .experiment_manifest_service import require_experiment_record
    from .experiment_campaign_models import ExperimentCampaign
    from .analysis_session_models import WorkflowAnalysisSession
    record = require_experiment_record(campaign, analysis)
    if campaign.evidence_tier != "experimental" or analysis.evidence_tier.value != "experimental":
        raise ValueError("Workflow experiments require a test environment; production connections are observation-only")
    # Execute the verified snapshots, not independently mutable live inputs.
    campaign = ExperimentCampaign.from_mapping(record.campaign_snapshot)
    analysis = WorkflowAnalysisSession.from_mapping(record.analysis_snapshot)
    definition: WorkflowProbeDefinition | None = campaign.execution_definition
    if definition is None:
        raise ValueError(
            "Campaign has no execution definition; create a new reviewed campaign"
        )
    resolved_runner = workload_runner or run_http_workflow_probe
    resolved_fetcher = trace_fetcher or _default_trace_fetcher
    resolved_sleeper = sleeper or time.sleep
    scenario = _scenario(campaign)
    run = resolved_runner(application_url, definition, started_at=timestamp)
    collected = collect_run_traces(
        run, definition, analysis, resolved_fetcher, resolved_sleeper,
        TRACE_POLL_DELAYS_SECONDS, verified_unavailable_service,
    )
    traces = collected.get(run.trace_id, [])

    registration = ExecutionRegistration.from_mapping(
        {
            "executionId": f"execution:{campaign.campaign_id.split(':')[-1]}:{uuid4().hex[:12]}",
            "batchId": batch_id,
            "workflowId": campaign.workflow_id,
            "scenarioVersion": 1,
            "invocationStatus": "completed",
            "source": "controlled_workflow_experiment",
            "evidenceLocator": (
                f"{analysis.trace_source.endpoint.rstrip('/')}"
                + {"jaeger": "/api/traces/", "zipkin": "/api/v2/trace/", "tempo": "/api/v2/traces/"}[analysis.trace_source.kind.value]
                + run.trace_id
            ),
            "environmentId": campaign.environment_id,
            "expectedTraceId": run.trace_id,
            "startedAt": run.started_at.isoformat(),
            "finishedAt": run.finished_at.isoformat(),
            "registeredBy": actor,
            "experimentRecordId": record.manifest_id,
            "experimentRecordDigest": record.content_digest,
        }
    )
    recorded_at = max(_utc(now), run.finished_at)
    outcome = _outcome_from_traces(registration, scenario, run, traces, recorded_at, verified_unavailable_service)
    if (
        not run.business_success
        and outcome.correlation_status is CorrelationStatus.MATCHED
        and outcome.evidence_snapshot is not None
        and (
            validate_trace_quality(
                [outcome.evidence_snapshot.to_mapping()["rawTrace"]]
            ).valid_count == 1
            or _expected_outage_trace_quality(
                outcome.evidence_snapshot.to_mapping()["rawTrace"],
                verified_unavailable_service,
            )
        )
        and outcome.status in {
            ExecutionOutcomeStatus.SUCCEEDED,
            ExecutionOutcomeStatus.INDETERMINATE,
        }
        and (
            outcome.status is ExecutionOutcomeStatus.SUCCEEDED
            or not trace_has_terminal_evidence(
                outcome.evidence_snapshot.to_mapping()["rawTrace"],
                scenario.success_condition,
            )
        )
    ):
        outcome = ExecutionOutcome(
            registration=outcome.registration,
            status=ExecutionOutcomeStatus.FAILED,
            correlation_status=outcome.correlation_status,
            recorded_at=outcome.recorded_at,
            matched_trace_id=outcome.matched_trace_id,
            evidence_id=outcome.evidence_id,
            evidence_snapshot=outcome.evidence_snapshot,
            assertions=(
                *outcome.assertions,
                AssertionResult(
                    name="business_probe_success",
                    passed=False,
                    expected=True,
                    observed=False,
                ),
            ),
            reasons=(
                "The reviewed workflow probe failed with a valid trace for its first failing step",
                run.reason,
            ),
        )
    # Retain affirmative probe evidence too: a successful HTTP trace does not
    # show that response-body or preparation-step assertions were checked.
    # This assertion never overrides an indeterminate trace outcome.
    if not any(item.name == "business_probe_success" for item in outcome.assertions):
        outcome = replace(
            outcome,
            assertions=(*outcome.assertions, AssertionResult(
                name="business_probe_success",
                passed=run.business_success,
                expected=True,
                observed=run.business_success,
            )),
        )
    outcome = replace(outcome, step_evidence=capture_step_evidence(
        run, definition, analysis, collected, recorded_at,
    ))
    blockers = sequence_evidence_blockers(outcome, verified_unavailable_service)
    if blockers:
        outcome = replace(
            outcome, status=ExecutionOutcomeStatus.INDETERMINATE,
            reasons=(*outcome.reasons, *blockers),
        )
    return execution_repository(repository).create(outcome)


def baseline_readiness(
    campaign: ExperimentCampaign,
    repository: ExecutionRepository | None = None,
) -> dict[str, Any]:
    """Is the normal (all services available) cohort complete and stable enough to compare against?

    Ready when it has the required number of eligible runs, no trace is reused by two runs, and its failure
    rate is at most the campaign's maximum (10%): an unstable workflow cannot show what one missing service
    changes, because failures would occur anyway.
    """
    records = execution_repository(repository).list(batch_id=campaign.baseline.batch_id)
    eligible = [item for item in records if _eligible_campaign_outcome(item)]
    successes = sum(item.status is ExecutionOutcomeStatus.SUCCEEDED for item in eligible)
    failures = sum(item.status is ExecutionOutcomeStatus.FAILED for item in eligible)
    excluded = len(records) - len(eligible)
    excluded_reasons = sorted({
        _campaign_exclusion_reason(item)
        for item in records if not _eligible_campaign_outcome(item)
    })
    # baseline success rate = successful eligible runs / eligible runs
    success_rate = successes / len(eligible) if eligible else None
    trace_ids = [trace_id for item in records for trace_id in item.trace_ids]
    duplicate_trace_ids = sorted(
        {trace_id for trace_id in trace_ids if trace_id and trace_ids.count(trace_id) > 1}
    )
    blockers: list[str] = []
    if len(eligible) < campaign.baseline.required_trials:
        blockers.append(
            f"Collect {campaign.baseline.required_trials - len(eligible)} more eligible baseline trial(s)."
        )
    if duplicate_trace_ids:
        blockers.append("Baseline contains a trace reused by multiple trial records.")
    # Stability gate: failure rate = 1 − success rate must not exceed the campaign maximum (10%)
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
        "excludedReasons": excluded_reasons,
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
    verified_unavailable_service: str | None = None,
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
    if verified_unavailable_service is not None and verified_unavailable_service != target_service:
        raise ValueError("Verified outage target must match the intervention service")
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
        verified_unavailable_service,
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
        for other_plan in campaign.interventions:
            if other_plan.target_service == plan.target_service:
                continue
            other_events = event_repository.list(campaign_id, other_plan.target_service)
            if other_events and other_events[-1].event_type is InterventionEventType.ACTIVATED:
                raise ValueError(
                    "Restore the active intervention for "
                    + other_plan.target_service
                    + " before activating another service"
                )
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


def _trial_within_intervention(
    outcome: ExecutionOutcome, events: list[InterventionEvent]
) -> bool:
    """Count a trial only if its full execution fits an attested outage window."""
    finished_at = outcome.registration.finished_at
    if finished_at is None:
        return False
    # Walk the outage events in time order; each 'activated' opens a window and the next 'restored' closes it.
    activated_at: datetime | None = None
    for event in events:
        if event.event_type is InterventionEventType.ACTIVATED:
            activated_at = event.occurred_at
        elif activated_at is not None:
            # The run counts only if it started after the outage began and finished before recovery was recorded.
            if activated_at <= outcome.registration.started_at and finished_at <= event.occurred_at:
                return True
            activated_at = None
    return (
        activated_at is not None
        and activated_at <= outcome.registration.started_at
    )


def _eligible_campaign_outcome(outcome: ExecutionOutcome) -> bool:
    """Accept only trace-backed outcomes produced by the controlled runner.

    A run counts as evidence only if all of these hold:
      - it was run by the backend under this experiment's frozen record (not recorded by hand);
      - its outcome is certain (SUCCEEDED or FAILED), from the exact trace the run created;
      - the stored trace snapshot is that trace, from the recorded trace backend;
      - every step of the workflow has its own matching trace (sequence evidence);
      - the trace is complete, or it misses spans only of the service verified to be down.
    """
    registration = outcome.registration
    snapshot = outcome.evidence_snapshot
    return (
        registration.source == "controlled_workflow_experiment"
        and registration.experiment_record_id is not None
        and registration.experiment_record_digest is not None
        and outcome.correlation_status is CorrelationStatus.MATCHED
        and outcome.status in {ExecutionOutcomeStatus.SUCCEEDED, ExecutionOutcomeStatus.FAILED}
        and outcome.matched_trace_id is not None
        and outcome.matched_trace_id == registration.expected_trace_id
        and snapshot is not None
        and snapshot.trace_id == outcome.matched_trace_id
        and snapshot.source_locator == registration.evidence_locator
        and not sequence_evidence_blockers(outcome, _verified_outage_target(outcome))
        and (
            validate_trace_quality([snapshot.to_mapping()["rawTrace"]]).valid_count == 1
            or _expected_outage_trace_quality(
                snapshot.to_mapping()["rawTrace"], _verified_outage_target(outcome)
            )
        )
    )


def _campaign_exclusion_reason(outcome: ExecutionOutcome) -> str:
    registration = outcome.registration
    blockers = sequence_evidence_blockers(outcome, _verified_outage_target(outcome))
    if blockers:
        return "; ".join(blockers)
    if registration.experiment_record_id is None or registration.experiment_record_digest is None:
        return "Trial predates the frozen experiment record and cannot be used for validation"
    if outcome.correlation_status is not CorrelationStatus.MATCHED:
        return "No unique matching trace was captured"
    if outcome.status not in {ExecutionOutcomeStatus.SUCCEEDED, ExecutionOutcomeStatus.FAILED}:
        return "The workflow outcome is indeterminate"
    if outcome.evidence_snapshot is None:
        return "The matched trace has no immutable evidence snapshot"
    if (
        validate_trace_quality([outcome.evidence_snapshot.to_mapping()["rawTrace"]]).valid_count != 1
        and not _expected_outage_trace_quality(
            outcome.evidence_snapshot.to_mapping()["rawTrace"],
            _verified_outage_target(outcome),
        )
    ):
        return "The matched trace is incomplete or invalid"
    return "Trial provenance does not satisfy the controlled evidence contract"


def _intervention_service_status(
    campaign: ExperimentCampaign,
    plan: Any,
    events: list[Any],
    execution_repository_override: ExecutionRepository | None,
) -> dict[str, Any]:
    state = "planned"
    if events:
        state = "active" if events[-1].event_type is InterventionEventType.ACTIVATED else "restored"
    records = execution_repository(execution_repository_override).list(batch_id=plan.batch_id)
    eligible = [
        item for item in records
        if _eligible_campaign_outcome(item) and _trial_within_intervention(item, events)
    ]
    trace_ids = [trace_id for item in records for trace_id in item.trace_ids]
    duplicate_trace_ids = sorted({trace_id for trace_id in trace_ids if trace_id and trace_ids.count(trace_id) > 1})
    blockers: list[str] = []
    if state != "restored":
        blockers.append("Intervention must be restored after the trials")
    if len(eligible) < campaign.baseline.required_trials:
        blockers.append("Collect more correlated trials within the confirmed intervention window")
    if duplicate_trace_ids:
        blockers.append("Intervention trials reuse a trace ID")
    excluded_reasons = sorted({
        _campaign_exclusion_reason(item)
        if not _eligible_campaign_outcome(item)
        else "Trial execution falls outside the confirmed intervention window"
        for item in records if item not in eligible
    })
    return {
        "targetService": plan.target_service,
        "batchId": plan.batch_id,
        "state": state,
        "trialCount": len(records),
        "eligibleTrials": len(eligible),
        "requiredTrials": campaign.baseline.required_trials,
        "excludedTrials": len(records) - len(eligible),
        "excludedReasons": excluded_reasons,
        "duplicateTraceIds": duplicate_trace_ids,
        "evidenceReady": not blockers,
        "blockers": blockers,
        "events": [event.to_mapping() for event in events],
    }


def intervention_status(
    campaign_id: str,
    *,
    campaign_repository: ExperimentCampaignRepository | None = None,
    repository: InterventionEventRepository | None = None,
    execution_repository_override: ExecutionRepository | None = None,
) -> dict[str, Any]:
    campaign = get_experiment_campaign(campaign_id, campaign_repository)
    event_repository = _intervention_repository(repository)
    services = [
        _intervention_service_status(
            campaign, plan, event_repository.list(campaign_id, plan.target_service), execution_repository_override,
        )
        for plan in campaign.interventions
    ]
    return {
        "campaignId": campaign.campaign_id,
        "baselineReady": baseline_readiness(
            campaign, execution_repository_override
        )["ready"],
        "services": services,
    }
