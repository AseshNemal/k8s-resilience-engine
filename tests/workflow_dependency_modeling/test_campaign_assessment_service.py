"""Campaign-to-assessment orchestration tests."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.engine.workflow_dependency_modeling.analysis_session_models import (
    WorkflowAnalysisSession,
)
from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    InMemoryAnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.campaign_assessment_service import (
    approve_experiment_campaign,
    assess_experiment_campaign,
    get_experiment_campaign_assessment,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    ValidatedDependencyType,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    InMemoryDependencyAssessmentRepository,
)
from app.engine.workflow_dependency_modeling.execution_models import (
    CorrelationStatus,
    ExecutionOutcome,
    ExecutionOutcomeStatus,
    ExecutionRegistration,
)
from app.engine.workflow_dependency_modeling.execution_repository import (
    InMemoryExecutionRepository,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_models import (
    ExperimentCampaign,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_repository import (
    InMemoryExperimentCampaignRepository,
)
from app.engine.workflow_dependency_modeling.intervention_event_models import (
    InterventionEvent,
)
from app.engine.workflow_dependency_modeling.intervention_event_repository import (
    InMemoryInterventionEventRepository,
)
from app.main import app


NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def _analysis() -> WorkflowAnalysisSession:
    return WorkflowAnalysisSession.from_mapping(
        {
            "sessionId": "analysis:checkout",
            "environmentId": "boutique-local",
            "evidenceTier": "experimental",
            "jaegerUrl": "http://localhost:16686",
            "entryService": "frontend",
            "operationName": "POST /cart/checkout",
            "lookback": "1h",
            "workflowId": "checkout",
            "traceCount": 5,
            "observedServices": ["frontend", "paymentservice"],
            "serviceEvidence": [],
            "edges": [{"source": "frontend", "target": "paymentservice"}],
            "maturity": "candidate",
            "nextAction": "Run controlled experiments",
            "limitations": [],
            "requiredActions": [],
            "createdAt": NOW.isoformat(),
            "createdBy": "researcher",
        }
    )


def _campaign() -> ExperimentCampaign:
    return ExperimentCampaign.from_mapping(
        {
            "campaignId": "campaign:checkout1",
            "analysisSessionId": "analysis:checkout",
            "environmentId": "boutique-local",
            "workflowId": "checkout",
            "workflowName": "Online Boutique checkout",
            "workloadProfile": "online_boutique_checkout_v0_10_1",
            "evidenceTier": "experimental",
            "observedTraceCount": 5,
            "candidateServices": ["paymentservice"],
            "successContract": {
                "outcome": "Customer receives a confirmed order",
                "protocol": "http",
                "acceptedStatusCodes": [200],
                "terminalService": "frontend",
                "terminalOperation": "POST /cart/checkout",
                "responseMarker": "Your order is complete!",
            },
            "baseline": {
                "batchId": "batch:baseline",
                "requiredTrials": 3,
                "maximumFailureRate": 0.1,
                "exactTraceCorrelationRequired": True,
            },
            "interventions": [
                {
                    "targetService": "paymentservice",
                    "interventionType": "service_unavailable",
                    "batchId": "batch:payment",
                    "status": "planned",
                    "operatorApprovalRequired": True,
                    "confirmationRequirements": ["confirm outage"],
                }
            ],
            "status": "baseline_required",
            "blockers": [],
            "createdAt": NOW.isoformat(),
            "createdBy": "researcher",
        }
    )


def _outcome(execution_id: str, batch_id: str, succeeded: bool) -> ExecutionOutcome:
    registration = ExecutionRegistration.from_mapping(
        {
            "executionId": execution_id,
            "batchId": batch_id,
            "workflowId": "checkout",
            "scenarioVersion": 1,
            "invocationStatus": "completed",
            "source": "controlled_online_boutique",
            "evidenceLocator": f"jaeger://{execution_id}",
            "environmentId": "boutique-local",
            "expectedTraceId": f"trace:{execution_id}",
            "startedAt": NOW.isoformat(),
            "finishedAt": NOW.isoformat(),
            "registeredBy": "runner",
        }
    )
    return ExecutionOutcome(
        registration=registration,
        status=(
            ExecutionOutcomeStatus.SUCCEEDED
            if succeeded
            else ExecutionOutcomeStatus.FAILED
        ),
        correlation_status=CorrelationStatus.MATCHED,
        recorded_at=NOW,
        matched_trace_id=registration.expected_trace_id,
        evidence_id=f"evidence:{execution_id}",
    )


def _repositories():
    analyses = InMemoryAnalysisSessionRepository()
    analyses.create(_analysis())
    campaigns = InMemoryExperimentCampaignRepository()
    campaigns.create(_campaign())
    executions = InMemoryExecutionRepository()
    for index in range(3):
        executions.create(_outcome(f"base:{index}", "batch:baseline", True))
        executions.create(_outcome(f"payment:{index}", "batch:payment", False))
    events = InMemoryInterventionEventRepository()
    for event_type, minute in (("activated", 1), ("restored", 2)):
        events.create(
            InterventionEvent.from_mapping(
                {
                    "eventId": f"event:{event_type}",
                    "campaignId": "campaign:checkout1",
                    "targetService": "paymentservice",
                    "batchId": "batch:payment",
                    "eventType": event_type,
                    "evidence": f"operator confirmed {event_type}",
                    "occurredAt": NOW.replace(minute=minute).isoformat(),
                    "recordedAt": NOW.replace(minute=minute).isoformat(),
                    "recordedBy": "operator",
                }
            )
        )
    return analyses, campaigns, executions, events


def test_completed_campaign_creates_explainable_assessment() -> None:
    analyses, campaigns, executions, events = _repositories()
    assessments = InMemoryDependencyAssessmentRepository()
    result = assess_experiment_campaign(
        "campaign:checkout1",
        actor="reviewer",
        campaign_repository=campaigns,
        analysis_repository=analyses,
        execution_repository_override=executions,
        event_repository=events,
        repository=assessments,
        now=NOW,
    )
    assert result.structural_entry_service == "frontend"
    assert result.candidate_graph.mandatory_services == ["frontend", "paymentservice"]
    assert result.services[0].classification is ValidatedDependencyType.MANDATORY
    assert result.services[0].evidence_strength == 1.0
    assert "activated=event:activated" in result.interventions[0].confirmation_evidence
    assert get_experiment_campaign_assessment(
        "campaign:checkout1",
        campaign_repository=campaigns,
        repository=assessments,
    ) == result

    approval = approve_experiment_campaign(
        "campaign:checkout1",
        actor="principal-investigator",
        rationale="Reviewed exact trace cohorts and causal classifications.",
        approval_confirmed=True,
        campaign_repository=campaigns,
        repository=assessments,
        now=NOW,
    )
    assert approval.approved_graph.mandatory_services == [
        "frontend",
        "paymentservice",
    ]
    assert approval.approved_graph.edges[0].source == "frontend"


def test_incomplete_intervention_cannot_be_assessed() -> None:
    analyses, campaigns, executions, _ = _repositories()
    with pytest.raises(ValueError, match="incomplete for: paymentservice"):
        assess_experiment_campaign(
            "campaign:checkout1",
            actor="reviewer",
            campaign_repository=campaigns,
            analysis_repository=analyses,
            execution_repository_override=executions,
            event_repository=InMemoryInterventionEventRepository(),
            repository=InMemoryDependencyAssessmentRepository(),
            now=NOW,
        )


def test_campaign_assessment_http_errors_are_documented() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    operation = paths[
        "/api/workflows/experiment-campaigns/{campaign_id}/assessment"
    ]["post"]
    assert set(operation["responses"]) >= {"201", "404", "409", "422"}
    approval = paths[
        "/api/workflows/experiment-campaigns/{campaign_id}/approval"
    ]["post"]
    assert set(approval["responses"]) >= {"201", "404", "409", "422"}
