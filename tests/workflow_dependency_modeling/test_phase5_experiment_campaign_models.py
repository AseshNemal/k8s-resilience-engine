"""Experiment campaign contract and persistence tests."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from app.engine.workflow_dependency_modeling.experiment_campaign_models import (
    ExperimentCampaign,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_repository import (
    InMemoryExperimentCampaignRepository,
    SqlAlchemyExperimentCampaignRepository,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_service import (
    create_experiment_campaign,
)
from app.engine.workflow_dependency_modeling.analysis_session_models import (
    WorkflowAnalysisSession,
)
from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    InMemoryAnalysisSessionRepository,
)
from fastapi.testclient import TestClient
from app.main import app
from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.shared.persistence.models import Base


def _campaign() -> ExperimentCampaign:
    return ExperimentCampaign.from_mapping(
        {
            "campaignId": "campaign:checkout:1",
            "analysisSessionId": "analysis:1",
            "environmentId": "online-boutique-local",
            "workflowId": "checkout",
            "workflowName": "Online Boutique checkout",
            "workloadProfile": "online_boutique_checkout_v0_10_1",
            "evidenceTier": "experimental",
            "observedTraceCount": 12,
            "candidateServices": ["paymentservice", "checkoutservice"],
            "successContract": {
                "outcome": "Customer receives an order confirmation",
                "protocol": "http",
                "acceptedStatusCodes": [200],
                "terminalService": "checkoutservice",
                "terminalOperation": "PlaceOrder",
            },
            "baseline": {
                "batchId": "batch:baseline:1",
                "requiredTrials": 5,
                "maximumFailureRate": 0.1,
                "exactTraceCorrelationRequired": True,
            },
            "interventions": [
                {
                    "targetService": service,
                    "interventionType": "service_unavailable",
                    "batchId": f"batch:intervention:{service}",
                    "status": "planned",
                    "operatorApprovalRequired": True,
                    "confirmationRequirements": [
                        "Record intervention start and end times",
                        "Confirm the target service was unavailable",
                    ],
                }
                for service in ("checkoutservice", "paymentservice")
            ],
            "status": "baseline_required",
            "blockers": ["Collect five exactly correlated baseline trials."],
            "createdAt": "2026-10-05T00:00:00Z",
            "createdBy": "researcher",
        }
    )


def test_campaign_requires_safe_interventions_and_exact_correlation() -> None:
    campaign = _campaign()
    assert campaign.baseline.exact_trace_correlation_required is True
    assert campaign.candidate_services == ("checkoutservice", "paymentservice")
    assert all(item.operator_approval_required for item in campaign.interventions)
    assert campaign.status.value == "baseline_required"


def test_campaign_rejects_unapproved_intervention() -> None:
    payload = _campaign().to_mapping()
    payload["interventions"][0]["operatorApprovalRequired"] = False
    with pytest.raises(ComponentDataError, match="operator approval"):
        ExperimentCampaign.from_mapping(payload)


def test_campaign_repositories_round_trip(tmp_path) -> None:
    campaign = _campaign()
    memory = InMemoryExperimentCampaignRepository()
    assert memory.create(campaign) == campaign
    assert memory.get(campaign.campaign_id) == campaign

    engine = create_engine(f"sqlite:///{tmp_path / 'campaigns.db'}")
    Base.metadata.create_all(engine)
    database = SqlAlchemyExperimentCampaignRepository(engine)
    assert database.create(campaign) == campaign
    assert database.get(campaign.campaign_id) == campaign
    assert database.list() == [campaign]


def _analysis() -> WorkflowAnalysisSession:
    return WorkflowAnalysisSession.from_mapping(
        {
            "sessionId": "analysis:checkout",
            "environmentId": "online-boutique-local",
            "evidenceTier": "experimental",
            "jaegerUrl": "http://localhost:16686",
            "entryService": "frontend",
            "operationName": "POST /cart/checkout",
            "lookback": "1h",
            "workflowId": "post_cart_checkout",
            "traceCount": 12,
            "observedServices": ["paymentservice", "frontend", "checkoutservice"],
            "serviceEvidence": [],
            "edges": [],
            "maturity": "observed",
            "nextAction": "confirm_success_condition",
            "limitations": ["Observation does not prove necessity."],
            "requiredActions": ["Collect a baseline."],
            "createdAt": "2026-10-05T00:00:00Z",
            "createdBy": "operator",
        }
    )


def test_service_derives_online_boutique_plan_from_observation() -> None:
    analyses = InMemoryAnalysisSessionRepository()
    analyses.create(_analysis())
    campaigns = InMemoryExperimentCampaignRepository()
    created = create_experiment_campaign(
        {
            "analysisSessionId": "analysis:checkout",
            "successContractConfirmed": True,
            "requiredBaselineTrials": 7,
        },
        actor="researcher",
        repository=campaigns,
        analysis_repository=analyses,
        now=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    assert created.workflow_id == "checkout"
    assert created.baseline.required_trials == 7
    assert created.candidate_services == ("checkoutservice", "paymentservice")
    assert created.success_contract.outcome == "Customer receives a confirmed order"
    assert created.blockers[0].startswith("Collect 7")


def test_service_rejects_non_checkout_observation() -> None:
    analyses = InMemoryAnalysisSessionRepository()
    payload = _analysis().to_mapping()
    payload["sessionId"] = "analysis:product"
    payload["operationName"] = "GET /product/{id}"
    analyses.create(WorkflowAnalysisSession.from_mapping(payload))
    with pytest.raises(ValueError, match="checkout operation"):
        create_experiment_campaign(
            {
                "analysisSessionId": "analysis:product",
                "successContractConfirmed": True,
            },
            actor="researcher",
            repository=InMemoryExperimentCampaignRepository(),
            analysis_repository=analyses,
        )


def test_experiment_campaign_api(monkeypatch) -> None:
    from app.engine.workflow_dependency_modeling import (
        analysis_session_service,
        experiment_campaign_service,
    )

    analyses = InMemoryAnalysisSessionRepository()
    analyses.create(_analysis())
    campaigns = InMemoryExperimentCampaignRepository()
    monkeypatch.setattr(analysis_session_service, "_default_repository", analyses)
    monkeypatch.setattr(experiment_campaign_service, "_default_repository", campaigns)
    client = TestClient(app)
    response = client.post(
        "/api/workflows/experiment-campaigns",
        json={
            "analysisSessionId": "analysis:checkout",
            "successContractConfirmed": True,
        },
        headers={"X-Actor": "researcher"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["workloadProfile"] == "online_boutique_checkout_v0_10_1"
    assert body["status"] == "baseline_required"
    assert client.get("/api/workflows/experiment-campaigns").json() == [body]
    assert client.get(
        f"/api/workflows/experiment-campaigns/{body['campaignId']}"
    ).json() == body


def test_experiment_campaign_http_exceptions_are_documented() -> None:
    responses = TestClient(app).get("/openapi.json").json()["paths"][
        "/api/workflows/experiment-campaigns"
    ]["post"]["responses"]
    assert {"201", "404", "409", "422"} <= responses.keys()
