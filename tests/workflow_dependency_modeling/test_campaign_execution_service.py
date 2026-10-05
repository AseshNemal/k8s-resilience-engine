"""Controlled baseline execution and readiness tests."""

from datetime import datetime, timedelta, timezone

from app.engine.workflow_dependency_modeling.analysis_session_models import (
    WorkflowAnalysisSession,
)
from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    InMemoryAnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.campaign_execution_service import (
    baseline_readiness,
    run_campaign_baseline_trial,
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


def _run(success=True) -> OnlineBoutiqueRunResult:
    return OnlineBoutiqueRunResult(
        trace_id=TRACE_ID,
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


def _trace(status=200):
    return {
        "traceID": TRACE_ID,
        "processes": {"p1": {"serviceName": "frontend"}},
        "spans": [
            {
                "traceID": TRACE_ID,
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
    outcome = run_campaign_baseline_trial(
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
    assert outcome.status is ExecutionOutcomeStatus.SUCCEEDED
    assert outcome.correlation_status is CorrelationStatus.MATCHED
    assert outcome.matched_trace_id == TRACE_ID
    assert executions.list(batch_id=campaign.baseline.batch_id) == [outcome]
    assert baseline_readiness(campaign, executions)["eligibleTrials"] == 1


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
