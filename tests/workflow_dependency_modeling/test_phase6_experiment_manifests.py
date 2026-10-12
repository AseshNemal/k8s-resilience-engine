"""Phase 6 reproducible experiment manifest tests."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from app.engine.workflow_dependency_modeling.analysis_session_models import (
    WorkflowAnalysisSession,
)
from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    InMemoryAnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_models import (
    ExperimentCampaign,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_repository import (
    InMemoryExperimentCampaignRepository,
)
from app.engine.workflow_dependency_modeling.experiment_manifest_models import (
    ExperimentManifest,
)
from app.engine.workflow_dependency_modeling.experiment_manifest_repository import (
    InMemoryExperimentManifestRepository,
    SqlAlchemyExperimentManifestRepository,
)
from app.engine.workflow_dependency_modeling.experiment_manifest_service import (
    create_experiment_manifest,
    verify_experiment_manifest,
)
from app.shared.persistence.models import Base


def _analysis() -> WorkflowAnalysisSession:
    return WorkflowAnalysisSession.from_mapping(
        {
            "sessionId": "analysis:orders",
            "environmentId": "retail-staging",
            "evidenceTier": "production",
            "jaegerUrl": "https://jaeger.example.test",
            "entryService": "gateway",
            "operationName": "POST /orders",
            "lookback": "1h",
            "workflowId": "place_order",
            "traceCount": 20,
            "observedServices": ["gateway", "orders", "payment"],
            "serviceEvidence": [],
            "traceQuality": {
                "totalCount": 20,
                "validCount": 20,
                "partialCount": 0,
                "invalidCount": 0,
                "excludedCount": 0,
                "results": [],
            },
            "edges": [{"source": "gateway", "target": "orders"}],
            "maturity": "observed",
            "nextAction": "confirm_success_condition",
            "limitations": ["Observation does not prove necessity."],
            "requiredActions": ["Collect controlled evidence."],
            "createdAt": "2026-10-12T01:00:00Z",
            "createdBy": "sre@example.test",
        }
    )


def _campaign() -> ExperimentCampaign:
    return ExperimentCampaign.from_mapping(
        {
            "campaignId": "campaign:orders",
            "analysisSessionId": "analysis:orders",
            "environmentId": "retail-staging",
            "workflowId": "place_order",
            "workflowName": "Place order",
            "workloadProfile": "reviewed_http_sequence",
            "evidenceTier": "production",
            "observedTraceCount": 20,
            "candidateServices": ["orders", "payment"],
            "successContract": {
                "outcome": "Order is confirmed",
                "condition": {
                    "protocol": "http",
                    "terminalService": "orders",
                    "operationName": "POST /orders",
                    "httpStatusCodes": [201],
                    "requiredAttributes": [],
                },
            },
            "executionDefinition": {
                "runnerType": "http_sequence",
                "steps": [
                    {
                        "name": "place_order",
                        "method": "POST",
                        "path": "/orders",
                        "expectedStatusCodes": [201],
                    }
                ],
                "successCondition": {
                    "protocol": "http",
                    "terminalService": "orders",
                    "operationName": "POST /orders",
                    "httpStatusCodes": [201],
                    "requiredAttributes": [],
                },
            },
            "baseline": {
                "batchId": "batch:orders:baseline",
                "requiredTrials": 5,
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
                    "confirmationRequirements": ["Confirm target unavailable"],
                }
                for service in ("orders", "payment")
            ],
            "status": "baseline_required",
            "blockers": ["Collect baseline evidence"],
            "createdAt": "2026-10-12T01:05:00Z",
            "createdBy": "researcher@example.test",
        }
    )


def _repositories():
    analyses = InMemoryAnalysisSessionRepository()
    analyses.create(_analysis())
    campaigns = InMemoryExperimentCampaignRepository()
    campaigns.create(_campaign())
    return analyses, campaigns, InMemoryExperimentManifestRepository()


def test_manifest_freezes_all_inputs_and_is_idempotent() -> None:
    analyses, campaigns, manifests = _repositories()
    controls = {
        "randomSeed": 42,
        "degradationRates": [0, 0.1, 0.25],
        "repetitionsPerCondition": 4,
    }
    first = create_experiment_manifest(
        "campaign:orders",
        controls,
        actor="researcher@example.test",
        repository=manifests,
        campaign_repository=campaigns,
        analysis_repository=analyses,
        now=datetime(2026, 10, 12, 2, tzinfo=timezone.utc),
    )
    second = create_experiment_manifest(
        "campaign:orders",
        controls,
        actor="another-operator@example.test",
        repository=manifests,
        campaign_repository=campaigns,
        analysis_repository=analyses,
    )

    assert first == second
    assert first.condition_count == 12
    assert first.analysis_snapshot["traceSource"]["type"] == "jaeger"
    assert first.campaign_snapshot["baseline"]["requiredTrials"] == 5
    assert verify_experiment_manifest(first) is True


def test_manifest_digest_detects_mutated_snapshot() -> None:
    analyses, campaigns, manifests = _repositories()
    manifest = create_experiment_manifest(
        "campaign:orders",
        {},
        actor="researcher@example.test",
        repository=manifests,
        campaign_repository=campaigns,
        analysis_repository=analyses,
    )
    payload = manifest.to_mapping()
    payload["analysisSnapshot"]["lookback"] = "24h"
    tampered = ExperimentManifest.from_mapping(payload)

    assert verify_experiment_manifest(tampered) is False


@pytest.mark.parametrize(
    "controls, message",
    [
        ({"randomSeed": -1}, "randomSeed"),
        ({"degradationRates": [0.2, 0.1]}, "unique and ascending"),
        ({"degradationRates": [0, 1.1]}, "between 0 and 1"),
        ({"repetitionsPerCondition": 0}, "between 1 and 100"),
        ({"unexpected": True}, "Unknown manifest controls"),
    ],
)
def test_manifest_rejects_non_reproducible_controls(controls, message) -> None:
    analyses, campaigns, manifests = _repositories()
    with pytest.raises((ValueError, TypeError), match=message):
        create_experiment_manifest(
            "campaign:orders",
            controls,
            actor="researcher@example.test",
            repository=manifests,
            campaign_repository=campaigns,
            analysis_repository=analyses,
        )


def test_manifest_repository_round_trips_with_sqlalchemy(tmp_path) -> None:
    analyses, campaigns, memory = _repositories()
    manifest = create_experiment_manifest(
        "campaign:orders",
        {},
        actor="researcher@example.test",
        repository=memory,
        campaign_repository=campaigns,
        analysis_repository=analyses,
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'manifests.db'}")
    Base.metadata.create_all(engine)
    database = SqlAlchemyExperimentManifestRepository(engine)

    assert database.create(manifest) == manifest
    assert database.get(manifest.manifest_id) == manifest
    assert database.list() == [manifest]
