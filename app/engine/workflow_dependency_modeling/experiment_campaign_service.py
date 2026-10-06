"""Create evidence-gated dependency experiment plans for reviewed workflows."""

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
from app.engine.workflow_dependency_modeling.online_boutique_workload import (
    online_boutique_checkout_probe,
)
from app.engine.workflow_dependency_modeling.workflow_onboarding import (
    build_workflow_probe,
)


ONLINE_BOUTIQUE_CHECKOUT_PROFILE = "online_boutique_checkout_v0_10_1"
_SERVER_FIELDS = {
    "campaignId",
    "environmentId",
    "workflowId",
    "workloadProfile",
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
    """Turn observed traces and a reviewed probe into an immutable plan."""
    forbidden = _SERVER_FIELDS & data.keys()
    if forbidden:
        raise ValueError(
            f"Server-managed fields are not accepted: {', '.join(sorted(forbidden))}"
        )
    session_id = data.get("analysisSessionId")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("analysisSessionId is required")
    if data.get("successContractConfirmed") is not True:
        raise ValueError("The workflow success contract must be confirmed")

    analysis = get_workflow_analysis(session_id.strip(), analysis_repository)
    raw_definition = data.get("executionDefinition")
    workflow_setup = data.get("workflowSetup")
    benchmark_profile = data.get("benchmarkProfile")
    selected_inputs = sum(
        item is not None
        for item in (raw_definition, workflow_setup, benchmark_profile)
    )
    if selected_inputs != 1:
        raise ValueError(
            "Provide exactly one of workflowSetup, executionDefinition, or benchmarkProfile"
        )

    if benchmark_profile is not None:
        if benchmark_profile != ONLINE_BOUTIQUE_CHECKOUT_PROFILE:
            raise ValueError("benchmarkProfile is not supported")
        if "checkout" not in analysis.operation_name.lower():
            raise ValueError(
                "The Online Boutique benchmark requires an observed checkout operation"
            )
        definition = online_boutique_checkout_probe(
            "OLJCESPC7Z",
            _timestamp(now).year + 2,
        )
        workflow_id = "checkout"
        workflow_name = "Online Boutique checkout"
        workload_profile = ONLINE_BOUTIQUE_CHECKOUT_PROFILE
        outcome = "Customer receives a confirmed order"
        response_marker = "Your order is complete!"
    else:
        if workflow_setup is not None:
            if not isinstance(workflow_setup, Mapping):
                raise ValueError("workflowSetup must be an object")
            definition = build_workflow_probe(
                analysis.entry_service,
                analysis.operation_name,
                workflow_setup,
            )
            setup_mode = str(workflow_setup.get("mode"))
        else:
            if not isinstance(raw_definition, Mapping):
                raise ValueError("executionDefinition must be an object")
            definition = build_workflow_probe(
                analysis.entry_service,
                analysis.operation_name,
                {
                    "mode": "imported_definition",
                    "executionDefinition": raw_definition,
                },
            )
            setup_mode = "imported_definition"
        workflow_id = analysis.workflow_id
        workflow_name = data.get("workflowName")
        if not isinstance(workflow_name, str) or not workflow_name.strip():
            raise ValueError("workflowName is required for a custom workflow")
        workflow_name = workflow_name.strip()
        outcome = data.get("businessOutcome")
        if not isinstance(outcome, str) or not outcome.strip():
            raise ValueError("businessOutcome is required for a custom workflow")
        outcome = outcome.strip()
        workload_profile = f"reviewed_{setup_mode}"
        response_marker = None

    terminal_service = definition.success_condition.terminal_service
    if terminal_service not in analysis.observed_services:
        raise ValueError(
            "The success condition terminal service was not present in observed traces"
        )
    candidates = tuple(
        sorted(service for service in analysis.observed_services if service != analysis.entry_service)
    )
    if not candidates:
        raise ValueError("No downstream services were observed for this workflow")

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
            "workflowId": workflow_id,
            "workflowName": workflow_name,
            "workloadProfile": workload_profile,
            "evidenceTier": analysis.evidence_tier.value,
            "observedTraceCount": analysis.trace_count,
            "candidateServices": list(candidates),
            "successContract": {
                "outcome": outcome,
                "condition": definition.success_condition.to_mapping(),
                **(
                    {"responseMarker": response_marker}
                    if response_marker is not None
                    else {}
                ),
            },
            "executionDefinition": definition.to_mapping(),
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
