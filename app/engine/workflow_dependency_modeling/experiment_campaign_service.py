"""Create evidence-gated Online Boutique dependency experiment plans."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    AnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.analysis_session_service import (
    get_workflow_analysis,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_models import (
    ExperimentCampaign,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_repository import (
    ExperimentCampaignRepository,
    SqlAlchemyExperimentCampaignRepository,
)


ONLINE_BOUTIQUE_CHECKOUT_PROFILE = "online_boutique_checkout_v0_10_1"
_SERVER_FIELDS = {
    "campaignId",
    "environmentId",
    "workflowId",
    "workflowName",
    "evidenceTier",
    "observedTraceCount",
    "candidateServices",
    "baseline",
    "interventions",
    "status",
    "blockers",
    "createdAt",
    "createdBy",
}

_default_repository: ExperimentCampaignRepository | None = None


def configure_experiment_campaign_repository(
    repository: ExperimentCampaignRepository,
) -> None:
    global _default_repository
    _default_repository = repository


def _repository(
    repository: ExperimentCampaignRepository | None,
) -> ExperimentCampaignRepository:
    if repository is not None:
        return repository
    global _default_repository
    if _default_repository is None:
        from app.core.config import get_settings
        from app.core.database import get_engine

        _default_repository = SqlAlchemyExperimentCampaignRepository(
            get_engine(get_settings().database_url)
        )
    return _default_repository


def _timestamp(now: datetime | None) -> datetime:
    result = now or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Experiment campaign timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def create_experiment_campaign(
    data: Mapping[str, Any],
    *,
    actor: str,
    repository: ExperimentCampaignRepository | None = None,
    analysis_repository: AnalysisSessionRepository | None = None,
    now: datetime | None = None,
) -> ExperimentCampaign:
    """Turn a checkout observation into a safe, immutable experiment plan."""
    forbidden = _SERVER_FIELDS & data.keys()
    if forbidden:
        raise ValueError(
            f"Server-managed fields are not accepted: {', '.join(sorted(forbidden))}"
        )
    session_id = data.get("analysisSessionId")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("analysisSessionId is required")
    if data.get("successContractConfirmed") is not True:
        raise ValueError("The Online Boutique checkout success contract must be confirmed")
    workload_profile = data.get("workloadProfile", ONLINE_BOUTIQUE_CHECKOUT_PROFILE)
    if workload_profile != ONLINE_BOUTIQUE_CHECKOUT_PROFILE:
        raise ValueError("Only the reviewed Online Boutique checkout profile is supported")

    analysis = get_workflow_analysis(session_id.strip(), analysis_repository)
    if "checkout" not in analysis.operation_name.lower():
        raise ValueError(
            "The Online Boutique checkout profile requires an observed checkout operation"
        )
    candidates = tuple(
        sorted(service for service in analysis.observed_services if service != analysis.entry_service)
    )
    if not candidates:
        raise ValueError("No downstream services were observed for this checkout operation")

    required_trials = data.get("requiredBaselineTrials", 5)
    if isinstance(required_trials, bool) or not isinstance(required_trials, int):
        raise ValueError("requiredBaselineTrials must be an integer")
    if not 3 <= required_trials <= 30:
        raise ValueError("requiredBaselineTrials must be between 3 and 30")

    timestamp = _timestamp(now)
    token = uuid4().hex
    campaign = ExperimentCampaign.from_mapping(
        {
            "campaignId": f"campaign:{token}",
            "analysisSessionId": analysis.session_id,
            "environmentId": analysis.environment_id,
            "workflowId": "checkout",
            "workflowName": "Online Boutique checkout",
            "workloadProfile": ONLINE_BOUTIQUE_CHECKOUT_PROFILE,
            "evidenceTier": analysis.evidence_tier.value,
            "observedTraceCount": analysis.trace_count,
            "candidateServices": list(candidates),
            "successContract": {
                "outcome": "Customer receives a confirmed order",
                "protocol": "http",
                "acceptedStatusCodes": [200],
                "terminalService": analysis.entry_service,
                "terminalOperation": analysis.operation_name,
                "responseMarker": "Your order is complete!",
            },
            "baseline": {
                "batchId": f"batch:{token}:baseline",
                "requiredTrials": required_trials,
                "maximumFailureRate": 0.1,
                "exactTraceCorrelationRequired": True,
            },
            "interventions": [
                {
                    "targetService": service,
                    "interventionType": "service_unavailable",
                    "batchId": f"batch:{token}:intervention:{service}",
                    "status": "planned",
                    "operatorApprovalRequired": True,
                    "confirmationRequirements": [
                        "Record the intervention start and end timestamps.",
                        f"Confirm {service} was unavailable during the trial window.",
                        "Restore and verify the service before the next intervention.",
                    ],
                }
                for service in candidates
            ],
            "status": "baseline_required",
            "blockers": [
                f"Collect {required_trials} successful, exactly correlated baseline trials.",
                "Confirm each intervention before collecting its evidence cohort.",
                "Complete every candidate-service cohort before dependency assessment.",
            ],
            "createdAt": timestamp.isoformat(),
            "createdBy": actor,
        }
    )
    return _repository(repository).create(campaign)


def get_experiment_campaign(
    campaign_id: str,
    repository: ExperimentCampaignRepository | None = None,
) -> ExperimentCampaign:
    return _repository(repository).get(campaign_id)


def list_experiment_campaigns(
    repository: ExperimentCampaignRepository | None = None,
) -> list[ExperimentCampaign]:
    return _repository(repository).list()
