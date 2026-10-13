"""Application-neutral campaign contracts outside the Online Boutique fixture."""

from app.engine.workflow_dependency_modeling.campaign_execution_service import _scenario
from app.engine.workflow_dependency_modeling.experiment_campaign_models import (
    ExperimentCampaign,
)


def _generic_campaign() -> ExperimentCampaign:
    return ExperimentCampaign.from_mapping(
        {
            "campaignId": "campaign:orders:1",
            "analysisSessionId": "analysis:orders:1",
            "environmentId": "staging-eu",
            "workflowId": "create_order",
            "workflowName": "Create customer order",
            "entryService": "api-gateway",
            "workloadProfile": "reviewed_imported_definition",
            "evidenceTier": "experimental",
            "observedTraceCount": 5,
            "candidateServices": ["inventory-api", "order-worker"],
            "successContract": {
                "outcome": "Order is accepted",
                "condition": {
                    "protocol": "span",
                    "terminalService": "order-worker",
                    "operationName": "order.persist",
                    "spanStatusCodes": ["OK"],
                },
            },
            "executionDefinition": {
                "runnerType": "http_sequence",
                "measuredStep": "submit",
                "initialVariables": {},
                "steps": [
                    {
                        "name": "submit",
                        "method": "POST",
                        "path": "/orders",
                        "expectedStatusCodes": [202],
                        "jsonBody": {"sku": "ABC-1"},
                    }
                ],
                "successCondition": {
                    "protocol": "span",
                    "terminalService": "order-worker",
                    "operationName": "order.persist",
                    "spanStatusCodes": ["OK"],
                },
            },
            "baseline": {
                "batchId": "batch:orders:baseline",
                "requiredTrials": 3,
                "maximumFailureRate": 0.1,
                "exactTraceCorrelationRequired": True,
            },
            "interventions": [
                {
                    "targetService": service,
                    "interventionType": "service_unavailable",
                    "batchId": f"batch:orders:{service}",
                    "status": "planned",
                    "operatorApprovalRequired": True,
                    "confirmationRequirements": ["Record activation and restoration"],
                }
                for service in ("inventory-api", "order-worker")
            ],
            "status": "baseline_required",
            "blockers": [],
            "createdAt": "2026-10-13T00:00:00Z",
            "createdBy": "researcher",
        }
    )


def test_generic_campaign_keeps_entry_and_terminal_services_separate() -> None:
    campaign = _generic_campaign()
    scenario = _scenario(campaign)

    assert campaign.entry_service == "api-gateway"
    assert scenario.entry_service == "api-gateway"
    assert scenario.success_condition.terminal_service == "order-worker"
    assert scenario.success_condition.operation_name == "order.persist"


def test_generic_campaign_round_trip_has_no_fixture_dependency() -> None:
    campaign = _generic_campaign()
    assert ExperimentCampaign.from_mapping(campaign.to_mapping()) == campaign
    assert campaign.workload_profile == "reviewed_imported_definition"
