"""Isolated, synthetic-only browser harness. Never stops services or touches Kubernetes.

Runs the real Component 1 router and algorithms against in-memory fixture cohorts.
This is engineering evidence, not an empirical failure experiment.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.api.workflows import router
from app.api.health import router as health_router
from app.web.workflows import router as web_router
from app.engine.workflow_dependency_modeling import service
from app.engine.workflow_dependency_modeling.analysis_session_models import WorkflowAnalysisSession
from app.engine.workflow_dependency_modeling.analysis_session_repository import InMemoryAnalysisSessionRepository
from app.engine.workflow_dependency_modeling.experiment_campaign_repository import InMemoryExperimentCampaignRepository
from app.engine.workflow_dependency_modeling.experiment_manifest_repository import InMemoryExperimentManifestRepository
from app.engine.workflow_dependency_modeling.execution_repository import InMemoryExecutionRepository
from app.engine.workflow_dependency_modeling.intervention_event_repository import InMemoryInterventionEventRepository
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import InMemoryDependencyAssessmentRepository
from app.engine.workflow_dependency_modeling.graph_freeze_repository import InMemoryGraphFreezeRepository
from app.engine.workflow_dependency_modeling.classification_evaluation_repository import InMemoryClassificationEvaluationRepository
from app.engine.workflow_dependency_modeling.scenario_repository import InMemoryScenarioRepository
from app.engine.workflow_dependency_modeling.models import WorkflowScenario
from app.engine.workflow_dependency_modeling.execution_models import ExecutionRegistration
from app.engine.workflow_dependency_modeling.outcome_evaluator import evaluate_execution
from app.engine.workflow_dependency_modeling.intervention_event_models import InterventionEvent


NOW = datetime.now(timezone.utc) - timedelta(hours=1)
analyses = InMemoryAnalysisSessionRepository()
campaigns = InMemoryExperimentCampaignRepository()
manifests = InMemoryExperimentManifestRepository()
executions = InMemoryExecutionRepository()
events = InMemoryInterventionEventRepository()
assessments = InMemoryDependencyAssessmentRepository()
scenarios = InMemoryScenarioRepository()
for configure, repository in (
    (service.configure_analysis_session_repository, analyses),
    (service.configure_experiment_campaign_repository, campaigns),
    (service.configure_experiment_manifest_repository, manifests),
    (service.configure_execution_repository, executions),
    (service.configure_intervention_event_repository, events),
    (service.configure_dependency_assessment_repository, assessments),
    (service.configure_scenario_repository, scenarios),
    (service.configure_graph_freeze_repository, InMemoryGraphFreezeRepository()),
    (service.configure_classification_evaluation_repository, InMemoryClassificationEvaluationRepository()),
):
    configure(repository)

scenario = WorkflowScenario.from_mapping({
    "workflowId": "browser_fixture_checkout", "description": "CONTROLLED FIXTURE: no real outage",
    "criticality": .8, "entryService": "fixture-gateway",
    "successCondition": {"protocol": "http", "terminalService": "fixture-gateway",
                         "operationName": "POST /fixture-checkout", "httpStatusCodes": [200]},
})
scenarios.create(scenario)
analyses.create(WorkflowAnalysisSession.from_mapping({
    "sessionId": "analysis:browser-controlled-fixture", "environmentId": "CONTROLLED-FIXTURE-ONLY",
    "evidenceTier": "experimental", "jaegerUrl": "http://localhost:16686",
    "entryService": "fixture-gateway", "operationName": "POST /fixture-checkout",
    "lookback": "1h", "workflowId": scenario.workflow_id,
    "traceCount": 3,
    "observedServices": ["fixture-gateway", "billing-api", "inventory-api", "notify-api"],
    "serviceEvidence": [], "traceQuality": {"totalCount": 3, "validCount": 3,
        "partialCount": 0, "invalidCount": 0, "excludedCount": 0, "results": []},
    "edges": [{"source": "fixture-gateway", "target": target}
              for target in ("billing-api", "inventory-api", "notify-api")],
    "maturity": "observed", "nextAction": "Controlled fixture browser test",
    "limitations": ["SYNTHETIC engineering fixture; no services have been stopped."],
    "requiredActions": [], "createdAt": NOW.isoformat(), "createdBy": "fixture-generator",
}))
campaign = service.create_experiment_campaign({
    "analysisSessionId": "analysis:browser-controlled-fixture",
    "successContractConfirmed": True, "requiredBaselineTrials": 3,
    "criticality": .65,
    "workflowName": "[CONTROLLED FIXTURE] Checkout lifecycle",
    "businessOutcome": "Synthetic checkout result matches its supplied outcome assertion",
    "workflowSetup": {"mode": "observed_entry", "expectedStatusCodes": [200], "request": {}},
}, actor="fixture-generator", now=NOW)
manifest = service.create_experiment_manifest(campaign.campaign_id, {}, actor="fixture-generator", now=NOW)
counter = 0
for target, batch_id, statuses in [
    (None, campaign.baseline.batch_id, [200, 200, 200]),
    *((plan.target_service, plan.batch_id,
       {"billing-api": [503, 503, 503], "inventory-api": [200, 200, 503], "notify-api": [200, 200, 200]}[plan.target_service])
      for plan in campaign.interventions),
]:
    for index, status in enumerate(statuses):
        counter += 1
        trace_id = f"{counter:032x}"
        trace = {"traceID": trace_id, "processes": {"p": {"serviceName": "fixture-gateway"}},
                 "spans": [{"spanID": "root", "processID": "p", "operationName": "POST /fixture-checkout",
                            "references": [], "tags": [{"key": "http.status_code", "value": status}]}]}
        instant = NOW + timedelta(seconds=(90 if target else 0) + index)
        registration = ExecutionRegistration.from_mapping({
            "executionId": f"fixture:{counter}", "batchId": batch_id, "workflowId": scenario.workflow_id,
            "scenarioVersion": 1, "environmentId": campaign.environment_id,
            "invocationStatus": "completed", "source": "controlled_workflow_experiment",
            "evidenceLocator": f"fixture://pp1/{trace_id}", "expectedTraceId": trace_id,
            "startedAt": instant.isoformat(), "finishedAt": instant.isoformat(),
            "registeredBy": "synthetic-fixture-generator",
            "experimentRecordId": manifest.manifest_id, "experimentRecordDigest": manifest.content_digest,
        })
        executions.create(evaluate_execution(registration, scenario, [trace], now=instant))
    if target:
        for event_type, seconds in (("activated", 60), ("restored", 120)):
            instant = NOW + timedelta(seconds=seconds)
            events.create(InterventionEvent.from_mapping({
                "eventId": f"fixture:{target}:{event_type}", "campaignId": campaign.campaign_id,
                "targetService": target, "batchId": batch_id, "eventType": event_type,
                "evidence": "CONTROLLED FIXTURE ONLY: simulated state window; no real service change",
                "occurredAt": instant.isoformat(), "recordedAt": instant.isoformat(),
                "recordedBy": "synthetic-fixture-generator",
            }))

app = FastAPI(title="Component 1 — CONTROLLED FIXTURE ONLY")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)
app.include_router(health_router)
app.include_router(web_router)

@app.middleware("http")
async def evidence_label(request, call_next):
    response = await call_next(request)
    response.headers["X-Evidence-Mode"] = "controlled-fixture-only"
    return response

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the isolated browser-test fixture server.")
    parser.add_argument("--port", type=int, default=8001)
    arguments = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=arguments.port)
