"""Campaign-to-assessment orchestration tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from app.engine.workflow_dependency_modeling import experiment_manifest_service as manifests
from app.engine.workflow_dependency_modeling.experiment_manifest_repository import InMemoryExperimentManifestRepository

from app.engine.workflow_dependency_modeling.analysis_session_models import (
    WorkflowAnalysisSession,
)
from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    InMemoryAnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.campaign_assessment_service import (
    _intervention_evidence,
    _intervention_verification,
    approve_experiment_campaign,
    assess_experiment_campaign,
    get_experiment_campaign_assessment,
    get_experiment_campaign_approval,
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
    TraceEvidenceSnapshot,
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


def test_progress_does_not_call_docker_verified_trials_operator_attested(monkeypatch):
    from app.engine.workflow_dependency_modeling import campaign_assessment_service as service

    records = [SimpleNamespace(marker="paymentservice")]
    monkeypatch.setattr(service, "execution_repository", lambda repository: SimpleNamespace(
        list=lambda *, batch_id: records
    ))
    monkeypatch.setattr(service, "_eligible_campaign_outcome", lambda outcome: True)
    monkeypatch.setattr(service, "_verified_outage_target", lambda outcome: outcome.marker)
    services = [{"batchId": "batch:payment", "targetService": "paymentservice", "evidenceReady": True}]
    assert _intervention_verification(services) == "docker_verified_local_demo"
    records.append(SimpleNamespace(marker=None))
    assert _intervention_verification(services) == "mixed"
    assert _intervention_evidence(services) == {
        "verification": "mixed", "verifiedTrials": 1, "eligibleTrials": 2,
        "services": [{"targetService": "paymentservice", "verifiedTrials": 1, "eligibleTrials": 2}],
    }
    records[0].marker = None
    assert _intervention_verification(services) == "operator_attested"


@pytest.fixture(autouse=True)
def isolated_experiment_manifests(monkeypatch):
    monkeypatch.setattr(manifests, "_default_repository", InMemoryExperimentManifestRepository())


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
            "traceQuality": {
                "totalCount": 5,
                "validCount": 5,
                "partialCount": 0,
                "invalidCount": 0,
                "excludedCount": 0,
                "results": [],
            },
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


def _outcome(execution_id: str, batch_id: str, succeeded: bool, manifest) -> ExecutionOutcome:
    started_at = NOW + (timedelta(seconds=90) if batch_id == "batch:payment" else timedelta())
    registration = ExecutionRegistration.from_mapping(
        {
            "executionId": execution_id,
            "batchId": batch_id,
            "workflowId": "checkout",
            "scenarioVersion": 1,
            "invocationStatus": "completed",
            "source": "controlled_workflow_experiment",
            "evidenceLocator": f"jaeger://{execution_id}",
            "environmentId": "boutique-local",
            "expectedTraceId": f"trace:{execution_id}",
            "startedAt": started_at.isoformat(),
            "finishedAt": started_at.isoformat(),
            "registeredBy": "runner",
            "experimentRecordId": manifest.manifest_id,
            "experimentRecordDigest": manifest.content_digest,
        }
    )
    trace = {
        "traceID": registration.expected_trace_id,
        "processes": {"p1": {"serviceName": "frontend"}},
        "spans": [{"spanID": f"span:{execution_id}", "processID": "p1", "references": []}],
    }
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
        evidence_snapshot=TraceEvidenceSnapshot.from_trace(
            trace,
            trace_id=registration.expected_trace_id,
            source_locator=registration.evidence_locator,
            captured_at=NOW,
        ),
    )


def _repositories():
    analyses = InMemoryAnalysisSessionRepository()
    analyses.create(_analysis())
    campaigns = InMemoryExperimentCampaignRepository()
    campaigns.create(_campaign())
    manifest = manifests.create_experiment_manifest(
        "campaign:checkout1", {}, actor="researcher",
        campaign_repository=campaigns, analysis_repository=analyses, now=NOW,
    )
    executions = InMemoryExecutionRepository()
    for index in range(3):
        executions.create(_outcome(f"base:{index}", "batch:baseline", True, manifest))
        executions.create(_outcome(f"payment:{index}", "batch:payment", False, manifest))
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
    assert result.source == "controlled_workflow_campaign"
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
    assert get_experiment_campaign_approval(
        "campaign:checkout1",
        campaign_repository=campaigns,
        repository=assessments,
    ) == approval


def test_incomplete_intervention_cannot_be_assessed() -> None:
    analyses, campaigns, executions, _ = _repositories()
    events = InMemoryInterventionEventRepository()
    assessments = InMemoryDependencyAssessmentRepository()
    with pytest.raises(ValueError, match="incomplete for: paymentservice"):
        assess_experiment_campaign(
            "campaign:checkout1",
            actor="reviewer",
            campaign_repository=campaigns,
            analysis_repository=analyses,
            execution_repository_override=executions,
            event_repository=events,
            repository=assessments,
            now=NOW,
        )


def test_legacy_trial_remains_excluded_through_assessment_and_freeze() -> None:
    from dataclasses import replace
    from app.engine.workflow_dependency_modeling.graph_freeze_service import freeze_workflow_graph
    from app.engine.workflow_dependency_modeling.graph_freeze_repository import InMemoryGraphFreezeRepository

    analyses, campaigns, executions, events = _repositories()
    manifest = manifests.list_experiment_manifests()[0]
    legacy = _outcome("base:legacy", "batch:baseline", True, manifest)
    legacy = replace(
        legacy,
        registration=replace(
            legacy.registration,
            experiment_record_id=None,
            experiment_record_digest=None,
        ),
    )
    executions.create(legacy)
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
    assert result.services[0].classification is ValidatedDependencyType.MANDATORY
    assert result.services[0].baseline.total_records == 4
    assert result.services[0].baseline.eligible_records == 3
    assert "base:legacy" in result.services[0].excluded_execution_ids
    approve_experiment_campaign(
        "campaign:checkout1",
        actor="reviewer",
        rationale="Reviewed the eligible evidence and the excluded legacy trial",
        approval_confirmed=True,
        campaign_repository=campaigns,
        repository=assessments,
        now=NOW,
    )
    frozen = freeze_workflow_graph(
        "checkout",
        {"freezeId": "freeze:checkout:legacy", "assessmentId": result.assessment_id},
        actor="reviewer",
        repository=InMemoryGraphFreezeRepository(),
        dependency_repository=assessments,
        execution_repository=executions,
        now=NOW,
    )
    assert frozen.valid_trace_count == 6
    assert frozen.excluded_trace_count == 1
    assert legacy.matched_trace_id not in frozen.trace_ids


def test_trials_outside_confirmed_intervention_window_cannot_be_assessed() -> None:
    from dataclasses import replace

    analyses, campaigns, executions, events = _repositories()
    for index in range(3):
        execution_id = f"payment:{index}"
        outcome = executions.get(execution_id)
        executions._records[execution_id] = replace(
            outcome,
            registration=replace(
                outcome.registration,
                started_at=NOW,
                finished_at=NOW,
            ),
        )
    with pytest.raises(ValueError, match="Intervention evidence is incomplete"):
        assess_experiment_campaign(
            "campaign:checkout1",
            actor="reviewer",
            campaign_repository=campaigns,
            analysis_repository=analyses,
            execution_repository_override=executions,
            event_repository=events,
            repository=InMemoryDependencyAssessmentRepository(),
            now=NOW,
        )


def test_assessment_rejects_trial_from_another_experiment_record() -> None:
    from dataclasses import replace

    analyses, campaigns, executions, events = _repositories()
    outcome = executions.get("payment:0")
    executions._records["payment:0"] = replace(
        outcome,
        registration=replace(
            outcome.registration,
            experiment_record_digest="sha256:" + "0" * 64,
        ),
    )
    with pytest.raises(ValueError, match="does not match the frozen experiment record"):
        assess_experiment_campaign(
            "campaign:checkout1",
            actor="reviewer",
            campaign_repository=campaigns,
            analysis_repository=analyses,
            execution_repository_override=executions,
            event_repository=events,
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
    assert "get" in paths[
        "/api/workflows/experiment-campaigns/{campaign_id}/approval"
    ]
