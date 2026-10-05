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
