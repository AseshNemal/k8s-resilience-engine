"""Create evidence-gated dependency experiment plans for reviewed workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    AnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.analysis_session_models import (
    WorkflowAnalysisSession,
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
from app.engine.workflow_dependency_modeling.workflow_probe_models import (
    WorkflowProbeDefinition,
)


ONLINE_BOUTIQUE_CHECKOUT_PROFILE = "online_boutique_checkout_v0_10_1"
_SERVER_FIELDS = {
    "campaignId",
    "environmentId",
    "workflowId",
    "entryService",
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


@dataclass(frozen=True)
class _CampaignSetup:
    definition: WorkflowProbeDefinition
    workflow_id: str
    workflow_name: str
    workload_profile: str
    outcome: str
    response_marker: str | None


def _required_campaign_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required for a custom workflow")
    return value.strip()


def _custom_probe(
    data: Mapping[str, Any], analysis: WorkflowAnalysisSession
) -> tuple[WorkflowProbeDefinition, str]:
    workflow_setup = data.get("workflowSetup")
    if workflow_setup is not None:
        if not isinstance(workflow_setup, Mapping):
            raise ValueError("workflowSetup must be an object")
        return (
            build_workflow_probe(
                analysis.entry_service, analysis.operation_name, workflow_setup
            ),
            str(workflow_setup.get("mode")),
        )
    raw_definition = data.get("executionDefinition")
    if not isinstance(raw_definition, Mapping):
        raise ValueError("executionDefinition must be an object")
    return (
        build_workflow_probe(
            analysis.entry_service,
            analysis.operation_name,
            {
                "mode": "imported_definition",
                "executionDefinition": raw_definition,
            },
        ),
        "imported_definition",
    )


def _campaign_setup(
    data: Mapping[str, Any],
    analysis: WorkflowAnalysisSession,
    now: datetime | None,
) -> _CampaignSetup:
    selected = (
        data.get("executionDefinition"),
        data.get("workflowSetup"),
        data.get("benchmarkProfile"),
    )
    if sum(item is not None for item in selected) != 1:
        raise ValueError(
            "Provide exactly one of workflowSetup, executionDefinition, or benchmarkProfile"
        )
    benchmark_profile = data.get("benchmarkProfile")
    if benchmark_profile is None:
        definition, setup_mode = _custom_probe(data, analysis)
        return _CampaignSetup(
            definition=definition,
            workflow_id=analysis.workflow_id,
            workflow_name=_required_campaign_text(data, "workflowName"),
            workload_profile=f"reviewed_{setup_mode}",
            outcome=_required_campaign_text(data, "businessOutcome"),
            response_marker=None,
        )
    if benchmark_profile != ONLINE_BOUTIQUE_CHECKOUT_PROFILE:
        raise ValueError("benchmarkProfile is not supported")
    if "checkout" not in analysis.operation_name.lower():
        raise ValueError(
            "The Online Boutique benchmark requires an observed checkout operation"
        )
    return _CampaignSetup(
        definition=online_boutique_checkout_probe(
            "OLJCESPC7Z", _timestamp(now).year + 2
        ),
        workflow_id="checkout",
        workflow_name="Online Boutique checkout",
        workload_profile=ONLINE_BOUTIQUE_CHECKOUT_PROFILE,
        outcome="Customer receives a confirmed order",
        response_marker="Your order is complete!",
    )


def _required_baseline_trials(data: Mapping[str, Any]) -> int:
    value = data.get("requiredBaselineTrials", 5)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("requiredBaselineTrials must be an integer")
    if not 3 <= value <= 30:
        raise ValueError("requiredBaselineTrials must be between 3 and 30")
    return value


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
    excluded_traces = analysis.trace_quality.get("excludedCount")
    if excluded_traces != 0:
        raise ValueError(
            "Dependency validation requires complete trace evidence; repair "
            "instrumentation and create a new analysis session"
        )
    setup = _campaign_setup(data, analysis, now)
    terminal_service = setup.definition.success_condition.terminal_service
    if terminal_service not in analysis.observed_services:
        raise ValueError(
            "The success condition terminal service was not present in observed traces"
        )
    candidates = tuple(
        sorted(service for service in analysis.observed_services if service != analysis.entry_service)
    )
    if not candidates:
        raise ValueError("No downstream services were observed for this workflow")

    required_trials = _required_baseline_trials(data)
    timestamp = _timestamp(now)
    token = uuid4().hex
    campaign = ExperimentCampaign.from_mapping(
        {
            "campaignId": f"campaign:{token}",
            "analysisSessionId": analysis.session_id,
            "environmentId": analysis.environment_id,
            "workflowId": setup.workflow_id,
            "workflowName": setup.workflow_name,
            "entryService": analysis.entry_service,
            "workloadProfile": setup.workload_profile,
            "evidenceTier": analysis.evidence_tier.value,
            "observedTraceCount": analysis.trace_count,
            "candidateServices": list(candidates),
            "successContract": {
                "outcome": setup.outcome,
                "condition": setup.definition.success_condition.to_mapping(),
                **(
                    {"responseMarker": setup.response_marker}
                    if setup.response_marker is not None
                    else {}
                ),
            },
            "executionDefinition": setup.definition.to_mapping(),
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


def preview_workflow_probe(
    session_id: str,
    setup: Mapping[str, Any],
    *,
    analysis_repository: AnalysisSessionRepository | None = None,
) -> dict[str, Any]:
    """Validate onboarding input and return the derived probe without persisting it."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("analysis session ID is required")
    analysis = get_workflow_analysis(session_id.strip(), analysis_repository)
    definition = build_workflow_probe(
        analysis.entry_service,
        analysis.operation_name,
        setup,
    )
    return {
        "analysisSessionId": analysis.session_id,
        "workflowId": analysis.workflow_id,
        "observedOperation": analysis.operation_name,
        "executionDefinition": definition.to_mapping(),
    }


def get_experiment_campaign(
    campaign_id: str,
    repository: ExperimentCampaignRepository | None = None,
) -> ExperimentCampaign:
    return _repository(repository).get(campaign_id)


def list_experiment_campaigns(
    repository: ExperimentCampaignRepository | None = None,
) -> list[ExperimentCampaign]:
    return _repository(repository).list()
