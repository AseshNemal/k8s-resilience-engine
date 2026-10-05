"""Convert a completed experiment campaign into an evidence-backed assessment."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.engine.workflow_dependency_modeling.analysis_session_repository import (
    AnalysisSessionRepository,
)
from app.engine.workflow_dependency_modeling.analysis_session_service import (
    get_workflow_analysis,
)
from app.engine.workflow_dependency_modeling.campaign_execution_service import (
    baseline_readiness,
    intervention_status,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentApproval,
    AssessmentPolicy,
    DependencyAssessment,
    InterventionCohort,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    DependencyAssessmentRepository,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_service import (
    _repository as assessment_repository,
    approve_dependency_assessment,
)
from app.engine.workflow_dependency_modeling.dependency_assessor import (
    assess_service_dependency,
)
from app.engine.workflow_dependency_modeling.execution_models import ExecutionOutcome
from app.engine.workflow_dependency_modeling.execution_repository import (
    ExecutionRepository,
)
from app.engine.workflow_dependency_modeling.execution_service import (
    _repository as execution_repository,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_repository import (
    ExperimentCampaignRepository,
)
from app.engine.workflow_dependency_modeling.experiment_campaign_service import (
    get_experiment_campaign,
)
from app.engine.workflow_dependency_modeling.intervention_event_repository import (
    InterventionEventRepository,
)


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Campaign assessment timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _validate_records(
    records: list[ExecutionOutcome], workflow_id: str, environment_id: str
) -> None:
    if not records:
        raise ValueError("Campaign evidence cohort is empty")
    for outcome in records:
        registration = outcome.registration
        if registration.workflow_id != workflow_id:
            raise ValueError("Campaign evidence contains a different workflow")
        if registration.environment_id != environment_id:
            raise ValueError("Campaign evidence contains a different environment")
        if registration.scenario_version != 1:
            raise ValueError("Campaign evidence contains an unsupported scenario version")


def _candidate_edges(
    edges: tuple[dict[str, str], ...], services: set[str]
) -> list[dict[str, str]]:
    result: set[tuple[str, str]] = set()
    for edge in edges:
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source in services and target in services and source != target:
            result.add((source, target))
    return [
        {"source": source, "target": target}
        for source, target in sorted(result)
    ]


def _assessment_id(campaign_id: str) -> str:
    return f"assessment:{campaign_id.split(':')[-1]}"


def assess_experiment_campaign(
    campaign_id: str,
    *,
    actor: str,
    campaign_repository: ExperimentCampaignRepository | None = None,
    analysis_repository: AnalysisSessionRepository | None = None,
    execution_repository_override: ExecutionRepository | None = None,
    event_repository: InterventionEventRepository | None = None,
    repository: DependencyAssessmentRepository | None = None,
    now: datetime | None = None,
) -> DependencyAssessment:
    """Classify every downstream candidate after all campaign gates pass."""
    campaign = get_experiment_campaign(campaign_id, campaign_repository)
    analysis = get_workflow_analysis(campaign.analysis_session_id, analysis_repository)
    baseline_state = baseline_readiness(campaign, execution_repository_override)
    if not baseline_state["ready"]:
        raise ValueError("Baseline evidence is not ready for assessment")

    intervention_state = intervention_status(
        campaign_id,
        campaign_repository=campaign_repository,
        repository=event_repository,
        execution_repository_override=execution_repository_override,
    )
    incomplete = [
        item["targetService"]
        for item in intervention_state["services"]
        if not item["evidenceReady"]
    ]
    if incomplete:
        raise ValueError(
            "Intervention evidence is incomplete for: " + ", ".join(incomplete)
        )

    execution_repo = execution_repository(execution_repository_override)
    baseline_records = execution_repo.list(batch_id=campaign.baseline.batch_id)
    _validate_records(baseline_records, campaign.workflow_id, campaign.environment_id)
    policy = AssessmentPolicy(min_samples_per_cohort=campaign.baseline.required_trials)
    service_results = []
    cohorts = []
    status_by_service = {
        item["targetService"]: item for item in intervention_state["services"]
    }
    for plan in campaign.interventions:
        state = status_by_service[plan.target_service]
        records = execution_repo.list(batch_id=plan.batch_id)
        _validate_records(records, campaign.workflow_id, campaign.environment_id)
        evidence = "; ".join(
            f"{event['eventType']}={event['eventId']} ({event['evidence']})"
            for event in state["events"]
        )
        cohort = InterventionCohort.from_mapping(
            {
                "targetService": plan.target_service,
                "interventionType": plan.intervention_type,
                "batchIds": [plan.batch_id],
                "interventionConfirmed": True,
                "confirmationEvidence": evidence,
            }
        )
        cohorts.append(cohort)
        service_results.append(
            assess_service_dependency(
                plan.target_service,
                baseline_records,
                cohort,
                records,
                policy,
            )
        )

    services = set(analysis.observed_services)
    expected = {analysis.entry_service, *campaign.candidate_services}
    if services != expected:
        raise ValueError("Campaign candidates no longer match the observed service set")
    assessment = DependencyAssessment.from_mapping(
        {
            "assessmentId": _assessment_id(campaign.campaign_id),
            "workflowId": campaign.workflow_id,
            "scenarioVersion": 1,
            "environmentId": campaign.environment_id,
            "source": "controlled_online_boutique_campaign",
            "candidateGraph": {
                "workflowId": campaign.workflow_id,
                "criticality": 1.0,
                "mandatoryServices": sorted(services),
                "optionalServices": [],
                "edges": _candidate_edges(analysis.edges, services),
            },
            "structuralEntryService": analysis.entry_service,
            "baselineBatchIds": [campaign.baseline.batch_id],
            "interventions": [item.to_mapping() for item in cohorts],
            "policy": policy.to_mapping(),
            "services": [item.to_mapping() for item in service_results],
            "createdAt": _utc(now).isoformat(),
            "createdBy": actor,
        }
    )
    return assessment_repository(repository).create_assessment(assessment)


def get_experiment_campaign_assessment(
    campaign_id: str,
    *,
    campaign_repository: ExperimentCampaignRepository | None = None,
    repository: DependencyAssessmentRepository | None = None,
) -> DependencyAssessment:
    """Return the assessment owned by a campaign, if it has been created."""
    get_experiment_campaign(campaign_id, campaign_repository)
    return assessment_repository(repository).get_assessment(
        _assessment_id(campaign_id)
    )


def approve_experiment_campaign(
    campaign_id: str,
    *,
    actor: str,
    rationale: str,
    approval_confirmed: bool,
    campaign_repository: ExperimentCampaignRepository | None = None,
    repository: DependencyAssessmentRepository | None = None,
    now: datetime | None = None,
) -> AssessmentApproval:
    """Approve only the assessment deterministically owned by this campaign."""
    if approval_confirmed is not True:
        raise ValueError("approvalConfirmed must be true after reviewing classifications")
    assessment = get_experiment_campaign_assessment(
        campaign_id,
        campaign_repository=campaign_repository,
        repository=repository,
    )
    if assessment.source != "controlled_online_boutique_campaign":
        raise ValueError("Campaign approval requires controlled campaign evidence")
    return approve_dependency_assessment(
        assessment.assessment_id,
        actor=actor,
        rationale=rationale,
        repository=repository,
        now=now,
    )


def get_experiment_campaign_approval(
    campaign_id: str,
    *,
    campaign_repository: ExperimentCampaignRepository | None = None,
    repository: DependencyAssessmentRepository | None = None,
) -> AssessmentApproval:
    """Return the immutable approved graph owned by this campaign."""
    get_experiment_campaign(campaign_id, campaign_repository)
    return assessment_repository(repository).get_approval(_assessment_id(campaign_id))
