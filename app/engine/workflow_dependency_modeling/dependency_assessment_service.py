"""Application service for evidence-backed dependency validation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentApproval,
    AssessmentPolicy,
    DependencyAssessment,
    InterventionCohort,
    ValidatedDependencyType,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    DependencyAssessmentNotApprovableError,
    DependencyAssessmentRepository,
    SqlAlchemyDependencyAssessmentRepository,
)
from app.engine.workflow_dependency_modeling.dependency_assessor import (
    assess_service_dependency,
)
from app.engine.workflow_dependency_modeling.execution_models import ExecutionOutcome
from app.engine.workflow_dependency_modeling.execution_repository import (
    ExecutionRepository,
)
from app.engine.workflow_dependency_modeling.execution_service import (
    list_executions,
)
from app.engine.workflow_dependency_modeling.scenario_service import (
    get_versioned_scenario,
)
from app.shared.schemas.workflow import WorkflowGraph


_default_repository: DependencyAssessmentRepository | None = None
_SERVER_FIELDS = {"services", "createdAt", "createdBy"}


def configure_dependency_assessment_repository(
    repository: DependencyAssessmentRepository,
) -> None:
    global _default_repository
    _default_repository = repository


def _repository(
    repository: DependencyAssessmentRepository | None,
) -> DependencyAssessmentRepository:
    if repository is not None:
        return repository
    global _default_repository
    if _default_repository is None:
        from app.core.config import get_settings
        from app.core.database import get_engine

        _default_repository = SqlAlchemyDependencyAssessmentRepository(
            get_engine(get_settings().database_url)
        )
    return _default_repository


def _utc(value: datetime | None = None) -> datetime:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None:
        raise ValueError("Dependency assessment timestamps must be timezone-aware")
    return resolved.astimezone(timezone.utc)


def _records_for_batches(
    batch_ids: tuple[str, ...],
    execution_repository: ExecutionRepository | None,
) -> list[ExecutionOutcome]:
    records: dict[str, ExecutionOutcome] = {}
    for batch_id in batch_ids:
        batch = list_executions(batch_id=batch_id, repository=execution_repository)
        if not batch:
            raise ValueError(f"Execution batch '{batch_id}' was not found")
        for outcome in batch:
            records[outcome.registration.execution_id] = outcome
    return list(records.values())


def _validate_records(
    records: list[ExecutionOutcome],
    workflow_id: str,
    scenario_version: int,
    environment_id: str,
) -> None:
    for outcome in records:
        registration = outcome.registration
        if registration.workflow_id != workflow_id:
            raise ValueError("Every execution cohort must belong to the assessed workflow")
        if registration.scenario_version != scenario_version:
            raise ValueError(
                "Every execution cohort must use the assessed scenario version"
            )
        if registration.environment_id != environment_id:
            raise ValueError(
                "Every execution cohort must use the assessed environment"
            )


def create_dependency_assessment(
    data: Mapping[str, Any],
    *,
    actor: str,
    repository: DependencyAssessmentRepository | None = None,
    execution_repository: ExecutionRepository | None = None,
    now: datetime | None = None,
) -> DependencyAssessment:
    """Create one immutable assessment from declared baseline/intervention batches."""
    forbidden = _SERVER_FIELDS & data.keys()
    if forbidden:
        raise ValueError(
            f"Server-managed fields are not accepted: {', '.join(sorted(forbidden))}"
        )
    graph = WorkflowGraph.model_validate(data.get("candidateGraph"))
    workflow_id = str(data.get("workflowId", "")).strip()
    environment_id = str(data.get("environmentId", "")).strip()
    scenario_version = data.get("scenarioVersion")
    if graph.workflow_id != workflow_id:
        raise ValueError("Candidate graph workflow ID does not match the assessment")
    if not isinstance(scenario_version, int) or isinstance(scenario_version, bool):
        raise ValueError("scenarioVersion must be a positive integer")
    get_versioned_scenario(workflow_id, scenario_version)

    baseline_ids = DependencyAssessment.from_mapping(
        {
            **data,
            "services": [],
            "createdAt": _utc(now).isoformat(),
            "createdBy": actor,
        }
    ).baseline_batch_ids
    interventions = tuple(
        InterventionCohort.from_mapping(item)
        for item in data.get("interventions", [])
    )
    intervention_services = [item.target_service for item in interventions]
    if len(intervention_services) != len(set(intervention_services)):
        raise ValueError("Only one intervention cohort is allowed per candidate service")
    intervention_batches = {
        batch_id for item in interventions for batch_id in item.batch_ids
    }
    if set(baseline_ids) & intervention_batches:
        raise ValueError("Baseline and intervention batches must not overlap")

    candidate_services = set(graph.mandatory_services + graph.optional_services)
    if not candidate_services:
        raise ValueError("Candidate graph must contain at least one service")
    unknown = set(intervention_services) - candidate_services
    if unknown:
        raise ValueError(
            f"Interventions target services outside the candidate graph: {', '.join(sorted(unknown))}"
        )

    baseline_records = _records_for_batches(baseline_ids, execution_repository)
    _validate_records(
        baseline_records, workflow_id, scenario_version, environment_id
    )
    policy = AssessmentPolicy.from_mapping(data.get("policy"))
    service_results = []
    for service_name in sorted(candidate_services):
        intervention = next(
            (item for item in interventions if item.target_service == service_name),
            None,
        )
        records = (
            _records_for_batches(intervention.batch_ids, execution_repository)
            if intervention
            else []
        )
        _validate_records(records, workflow_id, scenario_version, environment_id)
        service_results.append(
            assess_service_dependency(
                service_name,
                baseline_records,
                intervention,
                records,
                policy,
            )
        )

    assessment = DependencyAssessment.from_mapping(
        {
            **data,
            "policy": policy.to_mapping(),
            "services": [item.to_mapping() for item in service_results],
            "createdAt": _utc(now).isoformat(),
            "createdBy": actor,
        }
    )
    return _repository(repository).create_assessment(assessment)


def get_dependency_assessment(
    assessment_id: str,
    repository: DependencyAssessmentRepository | None = None,
) -> DependencyAssessment:
    return _repository(repository).get_assessment(assessment_id)


def list_dependency_assessments(
    workflow_id: str | None = None,
    repository: DependencyAssessmentRepository | None = None,
) -> list[DependencyAssessment]:
    return _repository(repository).list_assessments(workflow_id)


def approve_dependency_assessment(
    assessment_id: str,
    *,
    actor: str,
    rationale: str,
    repository: DependencyAssessmentRepository | None = None,
    now: datetime | None = None,
) -> AssessmentApproval:
    """Freeze a compatible graph only after every candidate has a decision."""
    repo = _repository(repository)
    assessment = repo.get_assessment(assessment_id)
    unresolved = [
        item.service_name
        for item in assessment.services
        if item.classification is ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    ]
    if unresolved:
        raise DependencyAssessmentNotApprovableError(
            "Assessment has insufficient evidence for: " + ", ".join(unresolved)
        )
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("Approval requires a non-empty rationale")

    mandatory = sorted(
        item.service_name
        for item in assessment.services
        if item.classification is ValidatedDependencyType.MANDATORY
    )
    optional = sorted(
        item.service_name
        for item in assessment.services
        if item.classification is ValidatedDependencyType.OPTIONAL
    )
    supporting = tuple(
        sorted(
            item.service_name
            for item in assessment.services
            if item.classification is ValidatedDependencyType.SUPPORTING
        )
    )
    retained = set(mandatory + optional)
    graph = WorkflowGraph(
        workflow_id=assessment.workflow_id,
        criticality=assessment.candidate_graph.criticality,
        mandatory_services=mandatory,
        optional_services=optional,
        edges=[
            edge
            for edge in assessment.candidate_graph.edges
            if edge.source in retained and edge.target in retained
        ],
    )
    approval = AssessmentApproval(
        assessment_id=assessment.assessment_id,
        workflow_id=assessment.workflow_id,
        scenario_version=assessment.scenario_version,
        approved_graph=graph,
        approved_at=_utc(now),
        approved_by=actor,
        rationale=rationale.strip(),
        excluded_supporting_services=supporting,
    )
    return repo.create_approval(approval)


def get_validated_workflow_graph(
    workflow_id: str,
    repository: DependencyAssessmentRepository | None = None,
) -> WorkflowGraph:
    return _repository(repository).latest_approval(workflow_id).approved_graph
