"""Controlled baseline execution and readiness tests."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.engine.workflow_dependency_modeling.analysis_session_models import (
    WorkflowAnalysisSession,
)
from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    InMemoryAnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.campaign_execution_service import (
    baseline_readiness,
    intervention_status,
    record_intervention_event,
    run_campaign_baseline_trial,
    run_campaign_intervention_trial,
)
from app.engine.workflow_dependency_modeling.execution_models import (
    CorrelationStatus,
    ExecutionOutcomeStatus,
)
from app.engine.workflow_dependency_modeling.execution_repository import (
    InMemoryExecutionRepository,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_repository import (
    InMemoryExperimentCampaignRepository,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_service import (
    create_experiment_campaign,
)
from app.engine.workflow_dependency_modeling.online_boutique_workload import (
    OnlineBoutiqueRunResult,
)
from app.engine.workflow_dependency_modeling.intervention_event_repository import (
    InMemoryInterventionEventRepository,
)
from app.main import app


NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)
TRACE_ID = "0123456789abcdef0123456789abcdef"


def _analysis_repository() -> InMemoryAnalysisSessionRepository:
    repository = InMemoryAnalysisSessionRepository()
    repository.create(
        WorkflowAnalysisSession.from_mapping(
            {
                "sessionId": "analysis:checkout",
                "environmentId": "online-boutique-local",
                "evidenceTier": "experimental",
                "jaegerUrl": "http://localhost:16686",
                "entryService": "frontend",
                "operationName": "POST /cart/checkout",
                "lookback": "1h",
                "workflowId": "post_cart_checkout",
                "traceCount": 8,
                "observedServices": ["frontend", "checkoutservice", "paymentservice"],
                "serviceEvidence": [],
                "edges": [],
                "maturity": "observed",
                "nextAction": "confirm_success_condition",
                "limitations": ["Observed only"],
                "requiredActions": ["Collect baseline"],
                "createdAt": NOW.isoformat(),
                "createdBy": "operator",
            }
        )
    )
    return repository


def _campaigns(analyses, required_trials=3):
    repository = InMemoryExperimentCampaignRepository()
    campaign = create_experiment_campaign(
        {
            "analysisSessionId": "analysis:checkout",
            "successContractConfirmed": True,
            "requiredBaselineTrials": required_trials,
        },
        actor="researcher",
        repository=repository,
        analysis_repository=analyses,
        now=NOW,
    )
    return repository, campaign


def _run(success=True, trace_id=TRACE_ID) -> OnlineBoutiqueRunResult:
    return OnlineBoutiqueRunResult(
        trace_id=trace_id,
        product_id="OLJCESPC7Z",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        product_status=200,
        cart_status=302,
        checkout_status=200,
        business_success=success,
        reason=(
            "Checkout completed and returned the reviewed order confirmation marker"
            if success
            else "Checkout response did not contain the reviewed order confirmation marker"
        ),
    )


def _trace(status=200, trace_id=TRACE_ID):
    return {
        "traceID": trace_id,
        "processes": {"p1": {"serviceName": "frontend"}},
        "spans": [
            {
                "traceID": trace_id,
                "spanID": "span-1",
                "operationName": "POST /cart/checkout",
                "processID": "p1",
                "startTime": 1_000_000,
                "duration": 100_000,
                "tags": [
                    {"key": "http.status_code", "type": "int64", "value": status}
                ],
            }
        ],
    }


def test_baseline_trial_executes_correlates_and_persists() -> None:
    analyses = _analysis_repository()
    campaigns, campaign = _campaigns(analyses)
    executions = InMemoryExecutionRepository()
    received_definition = None

    def runner(application_url, definition, **kwargs):
        nonlocal received_definition
        received_definition = definition
        return _run()

    outcome = run_campaign_baseline_trial(
        campaign.campaign_id,
        "http://localhost:8080",
        actor="runner",
        campaign_repository=campaigns,
        analysis_repository=analyses,
        repository=executions,
        workload_runner=runner,
        trace_fetcher=lambda analysis, trace_id: [_trace()],
        sleeper=lambda seconds: None,
        now=NOW,
    )
    assert outcome.status is ExecutionOutcomeStatus.SUCCEEDED
    assert outcome.correlation_status is CorrelationStatus.MATCHED
    assert outcome.matched_trace_id == TRACE_ID
    assert outcome.registration.source == "controlled_workflow_experiment"
    assert received_definition == campaign.execution_definition
    assert executions.list(batch_id=campaign.baseline.batch_id) == [outcome]
    assert baseline_readiness(campaign, executions)["eligibleTrials"] == 1


def test_default_trace_fetcher_uses_saved_trace_source(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import campaign_execution_service

    analysis = _analysis_repository().get("analysis:checkout")
    captured = None

    class Source:
        def fetch_workflow_traces(self, **query):
            nonlocal captured
            captured = query
            return []

    monkeypatch.setattr(
        campaign_execution_service.JaegerTraceSource,
        "from_configuration",
        lambda configuration: Source(),
    )

    assert campaign_execution_service._default_trace_fetcher(
        analysis, TRACE_ID
    ) == []
    assert captured == {
        "service_name": "frontend",
        "operation": "POST /cart/checkout",
        "lookback": "1h",
        "limit": 100,
    }


def test_missing_trace_is_indeterminate_and_blocks_readiness() -> None:
    analyses = _analysis_repository()
    campaigns, campaign = _campaigns(analyses)
    executions = InMemoryExecutionRepository()
    outcome = run_campaign_baseline_trial(
        campaign.campaign_id,
        "http://localhost:8080",
        actor="runner",
        campaign_repository=campaigns,
        analysis_repository=analyses,
        repository=executions,
        workload_runner=lambda *args, **kwargs: _run(),
        trace_fetcher=lambda analysis, trace_id: [],
        sleeper=lambda seconds: None,
        now=NOW,
    )
    readiness = baseline_readiness(campaign, executions)
    assert outcome.status is ExecutionOutcomeStatus.INDETERMINATE
    assert outcome.correlation_status is CorrelationStatus.MISSING
    assert readiness["ready"] is False
    assert readiness["excludedTrials"] == 1
    assert any("uncorrelated" in blocker for blocker in readiness["blockers"])


def test_confirmation_marker_can_fail_matched_http_trace() -> None:
    analyses = _analysis_repository()
    campaigns, campaign = _campaigns(analyses)
    outcome = run_campaign_baseline_trial(
        campaign.campaign_id,
        "http://localhost:8080",
        actor="runner",
        campaign_repository=campaigns,
        analysis_repository=analyses,
        repository=InMemoryExecutionRepository(),
        workload_runner=lambda *args, **kwargs: _run(success=False),
        trace_fetcher=lambda analysis, trace_id: [_trace()],
        sleeper=lambda seconds: None,
        now=NOW,
    )
    assert outcome.correlation_status is CorrelationStatus.MATCHED
    assert outcome.status is ExecutionOutcomeStatus.FAILED
    assert "confirmation marker" in outcome.reasons[-1]


def test_campaign_baseline_api_runs_and_reports_readiness(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import (
        analysis_session_service,
        campaign_execution_service,
        execution_service,
        experiment_campaign_service,
    )

    analyses = _analysis_repository()
    campaigns, campaign = _campaigns(analyses)
    executions = InMemoryExecutionRepository()
    monkeypatch.setattr(analysis_session_service, "_default_repository", analyses)
    monkeypatch.setattr(experiment_campaign_service, "_default_repository", campaigns)
    monkeypatch.setattr(execution_service, "_default_repository", executions)
    monkeypatch.setattr(
        campaign_execution_service,
        "run_http_workflow_probe",
        lambda *args, **kwargs: _run(),
    )
    monkeypatch.setattr(
        campaign_execution_service,
        "_default_trace_fetcher",
        lambda analysis, trace_id: [_trace()],
    )
    client = TestClient(app)
    created = client.post(
        f"/api/workflows/experiment-campaigns/{campaign.campaign_id}/baseline-runs",
        json={"applicationUrl": "http://localhost:8080"},
        headers={"X-Actor": "runner"},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "succeeded"
    readiness = client.get(
        f"/api/workflows/experiment-campaigns/{campaign.campaign_id}/baseline-readiness"
    )
    assert readiness.status_code == 200
    assert readiness.json()["eligibleTrials"] == 1
    assert readiness.json()["ready"] is False
    conflict = client.post(
        f"/api/workflows/experiment-campaigns/{campaign.campaign_id}/baseline-runs",
        json={
            "applicationUrl": "http://localhost:8080",
            "frontendUrl": "http://localhost:8081",
        },
        headers={"X-Actor": "runner"},
    )
    assert conflict.status_code == 422
    assert "cannot conflict" in conflict.json()["detail"]


def test_campaign_baseline_http_exceptions_are_documented() -> None:
    responses = TestClient(app).get("/openapi.json").json()["paths"][
        "/api/workflows/experiment-campaigns/{campaign_id}/baseline-runs"
    ]["post"]["responses"]
    assert {"201", "404", "409", "422", "503"} <= responses.keys()


def test_duplicate_trace_reuse_blocks_baseline_readiness() -> None:
    analyses = _analysis_repository()
    campaigns, campaign = _campaigns(analyses)
    executions = InMemoryExecutionRepository()
    for _ in range(3):
        run_campaign_baseline_trial(
            campaign.campaign_id,
            "http://localhost:8080",
            actor="runner",
            campaign_repository=campaigns,
            analysis_repository=analyses,
            repository=executions,
            workload_runner=lambda *args, **kwargs: _run(),
            trace_fetcher=lambda analysis, trace_id: [_trace()],
            sleeper=lambda seconds: None,
            now=NOW,
        )
    readiness = baseline_readiness(campaign, executions)
    assert readiness["ready"] is False
    assert readiness["duplicateTraceIds"] == [TRACE_ID]


def _ready_baseline(analyses, campaigns, campaign, executions) -> None:
    trace_ids = [f"{index:032x}" for index in range(1, 4)]
    for trace_id in trace_ids:
        run_campaign_baseline_trial(
            campaign.campaign_id,
            "http://localhost:8080",
            actor="runner",
            campaign_repository=campaigns,
            analysis_repository=analyses,
            repository=executions,
            workload_runner=lambda *args, _trace_id=trace_id, **kwargs: _run(trace_id=_trace_id),
            trace_fetcher=lambda analysis, expected, _trace_id=trace_id: [_trace(trace_id=_trace_id)],
            sleeper=lambda seconds: None,
            now=NOW,
        )


def test_intervention_events_require_ready_baseline_and_safe_order() -> None:
    analyses = _analysis_repository()
    campaigns, campaign = _campaigns(analyses)
    executions = InMemoryExecutionRepository()
    events = InMemoryInterventionEventRepository()
    _ready_baseline(analyses, campaigns, campaign, executions)
    activated = record_intervention_event(
        campaign.campaign_id,
        {
            "targetService": "paymentservice",
            "eventType": "activated",
            "operatorConfirmed": True,
            "evidence": "docker:paymentservice-stopped",
            "occurredAt": NOW.isoformat(),
        },
        actor="operator",
        campaign_repository=campaigns,
        repository=events,
        execution_repository_override=executions,
        now=NOW,
    )
    restored = record_intervention_event(
        campaign.campaign_id,
        {
            "targetService": "paymentservice",
            "eventType": "restored",
            "operatorConfirmed": True,
            "evidence": "docker:paymentservice-healthy",
            "occurredAt": (NOW + timedelta(minutes=1)).isoformat(),
        },
        actor="operator",
        campaign_repository=campaigns,
        repository=events,
        execution_repository_override=executions,
        now=NOW + timedelta(minutes=1),
    )
    assert activated.batch_id == restored.batch_id
    status = intervention_status(
        campaign.campaign_id,
        campaign_repository=campaigns,
        repository=events,
        execution_repository_override=executions,
    )
    payment = next(item for item in status["services"] if item["targetService"] == "paymentservice")
    assert status["baselineReady"] is True
    assert payment["state"] == "restored"
    assert len(payment["events"]) == 2


def test_intervention_event_api_records_operator_confirmation(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import (
        analysis_session_service,
        campaign_execution_service,
        execution_service,
        experiment_campaign_service,
    )

    analyses = _analysis_repository()
    campaigns, campaign = _campaigns(analyses)
    executions = InMemoryExecutionRepository()
    events = InMemoryInterventionEventRepository()
    _ready_baseline(analyses, campaigns, campaign, executions)
    monkeypatch.setattr(analysis_session_service, "_default_repository", analyses)
    monkeypatch.setattr(experiment_campaign_service, "_default_repository", campaigns)
    monkeypatch.setattr(execution_service, "_default_repository", executions)
    monkeypatch.setattr(
        campaign_execution_service, "_default_intervention_repository", events
    )
    intervention_trace_id = "00000000000000000000000000000020"
    monkeypatch.setattr(
        campaign_execution_service,
        "run_http_workflow_probe",
        lambda *args, **kwargs: _run(False, intervention_trace_id),
    )
    monkeypatch.setattr(
        campaign_execution_service,
        "_default_trace_fetcher",
        lambda analysis, trace_id: [_trace(500, intervention_trace_id)],
    )
    client = TestClient(app)
    created = client.post(
        f"/api/workflows/experiment-campaigns/{campaign.campaign_id}/intervention-events",
        json={
            "targetService": "paymentservice",
            "eventType": "activated",
            "operatorConfirmed": True,
            "evidence": "docker:paymentservice-stopped",
            "occurredAt": NOW.isoformat(),
        },
        headers={"X-Actor": "operator"},
    )
    assert created.status_code == 201
    assert created.json()["eventType"] == "activated"
    trial = client.post(
        f"/api/workflows/experiment-campaigns/{campaign.campaign_id}/interventions/paymentservice/runs",
        json={"applicationUrl": "http://localhost:8080"},
        headers={"X-Actor": "runner"},
    )
    assert trial.status_code == 201
    assert trial.json()["status"] == "failed"
    status = client.get(
        f"/api/workflows/experiment-campaigns/{campaign.campaign_id}/intervention-status"
    )
    assert status.status_code == 200
    payment = next(
        item for item in status.json()["services"]
        if item["targetService"] == "paymentservice"
    )
    assert payment["state"] == "active"


def test_intervention_event_http_exceptions_are_documented() -> None:
    responses = TestClient(app).get("/openapi.json").json()["paths"][
        "/api/workflows/experiment-campaigns/{campaign_id}/intervention-events"
    ]["post"]["responses"]
    assert {"201", "404", "409", "422"} <= responses.keys()


def test_intervention_trial_requires_active_event_and_updates_status() -> None:
    analyses = _analysis_repository()
    campaigns, campaign = _campaigns(analyses)
    executions = InMemoryExecutionRepository()
    events = InMemoryInterventionEventRepository()
    _ready_baseline(analyses, campaigns, campaign, executions)
    record_intervention_event(
        campaign.campaign_id,
        {
            "targetService": "paymentservice",
            "eventType": "activated",
            "operatorConfirmed": True,
            "evidence": "docker:paymentservice-stopped",
            "occurredAt": NOW.isoformat(),
        },
        actor="operator",
        campaign_repository=campaigns,
        repository=events,
        execution_repository_override=executions,
        now=NOW,
    )
    trace_id = "00000000000000000000000000000010"
    outcome = run_campaign_intervention_trial(
        campaign.campaign_id,
        "paymentservice",
        "http://localhost:8080",
        actor="runner",
        campaign_repository=campaigns,
        analysis_repository=analyses,
        event_repository=events,
        repository=executions,
        workload_runner=lambda *args, **kwargs: _run(False, trace_id),
        trace_fetcher=lambda analysis, expected: [_trace(500, trace_id)],
        sleeper=lambda seconds: None,
        now=NOW,
    )
    assert outcome.status is ExecutionOutcomeStatus.FAILED
    status = intervention_status(
        campaign.campaign_id,
        campaign_repository=campaigns,
        repository=events,
        execution_repository_override=executions,
    )
    payment = next(item for item in status["services"] if item["targetService"] == "paymentservice")
    assert payment["state"] == "active"
    assert payment["eligibleTrials"] == 1
    assert payment["evidenceReady"] is False


def test_intervention_run_http_exceptions_are_documented() -> None:
    responses = TestClient(app).get("/openapi.json").json()["paths"][
        "/api/workflows/experiment-campaigns/{campaign_id}/interventions/{target_service}/runs"
    ]["post"]["responses"]
    assert {"201", "404", "409", "422", "503"} <= responses.keys()
