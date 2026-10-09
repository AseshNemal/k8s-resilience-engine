"""Application service for evidence-backed dependency validation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentApproval,
    AssessmentPolicy,
    DependencyAssessment,
    EdgeInterventionCohort,
    InterventionCohort,
    ValidatedDependencyType,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    DependencyAssessmentNotApprovableError,
    DependencyAssessmentRepository,
    SqlAlchemyDependencyAssessmentRepository,
)
from app.engine.workflow_dependency_modeling.dependency_assessor import (
    assess_edge_dependency,
    assess_service_dependency,
)
from app.engine.workflow_dependency_modeling.execution_models import ExecutionOutcome
from app.engine.workflow_dependency_modeling.execution_repository import (
    ExecutionNotFoundError,
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
_SERVER_FIELDS = {"services", "edges", "createdAt", "createdBy"}


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


def dependency_assessment_repository(
    repository: DependencyAssessmentRepository | None = None,
) -> DependencyAssessmentRepository:
    """Return the configured repository for collaborating Component 1 services."""
    return _repository(repository)


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
            raise ExecutionNotFoundError(
                f"Execution batch '{batch_id}' was not found"
            )
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


def _assessment_scope(
    graph: WorkflowGraph,
    baseline_ids: tuple[str, ...],
    interventions: tuple[InterventionCohort, ...],
    edge_interventions: tuple[EdgeInterventionCohort, ...],
    structural_entry_service: str | None,
) -> set[str]:
    intervention_services = [item.target_service for item in interventions]
    if len(intervention_services) != len(set(intervention_services)):
        raise ValueError("Only one intervention cohort is allowed per candidate service")
    edge_keys = [
        (item.source_service, item.target_service) for item in edge_interventions
    ]
    if len(edge_keys) != len(set(edge_keys)):
        raise ValueError("Only one intervention cohort is allowed per candidate edge")
    all_interventions = (*interventions, *edge_interventions)
    intervention_batch_list = [
        batch_id for item in all_interventions for batch_id in item.batch_ids
    ]
    intervention_batches = set(intervention_batch_list)
    if set(baseline_ids) & intervention_batches:
        raise ValueError("Baseline and intervention batches must not overlap")
    if len(intervention_batch_list) != len(intervention_batches):
        raise ValueError("An intervention batch may belong to only one cohort")
    candidate_services = set(graph.mandatory_services + graph.optional_services)
    if not candidate_services:
        raise ValueError("Candidate graph must contain at least one service")
    unknown = set(intervention_services) - candidate_services
    if unknown:
        raise ValueError(
            "Interventions target services outside the candidate graph: "
            + ", ".join(sorted(unknown))
        )
    candidate_edges = {(edge.source, edge.target) for edge in graph.edges}
    unknown_edges = set(edge_keys) - candidate_edges
    if unknown_edges:
        formatted = ", ".join(
            f"{source}->{target}" for source, target in sorted(unknown_edges)
        )
        raise ValueError(
            "Edge interventions target dependencies outside the candidate graph: "
            + formatted
        )
    if structural_entry_service not in candidate_services | {None}:
        raise ValueError("Structural entry service must be present in the candidate graph")
    if structural_entry_service in intervention_services:
        raise ValueError("Structural entry service must not have an intervention cohort")
    return candidate_services


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
    edge_interventions = tuple(
        EdgeInterventionCohort.from_mapping(item)
        for item in data.get("edgeInterventions", [])
    )
    structural_entry_service = data.get("structuralEntryService")
    candidate_services = _assessment_scope(
        graph,
        baseline_ids,
        interventions,
        edge_interventions,
        structural_entry_service,
    )

    baseline_records = _records_for_batches(baseline_ids, execution_repository)
    _validate_records(
        baseline_records, workflow_id, scenario_version, environment_id
    )
    policy = AssessmentPolicy.from_mapping(data.get("policy"))
    service_results = []
    for service_name in sorted(candidate_services - {structural_entry_service}):
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

    edge_results = []
    for edge in sorted(graph.edges, key=lambda item: (item.source, item.target)):
        intervention = next(
            (
                item
                for item in edge_interventions
                if (item.source_service, item.target_service)
                == (edge.source, edge.target)
            ),
            None,
        )
        records = (
            _records_for_batches(intervention.batch_ids, execution_repository)
            if intervention
            else []
        )
        _validate_records(records, workflow_id, scenario_version, environment_id)
        edge_results.append(
            assess_edge_dependency(
                edge.source,
                edge.target,
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
            "edges": [item.to_mapping() for item in edge_results],
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
    if assessment.edge_interventions:
        unresolved_edges = [
            f"{item.source_service}->{item.target_service}"
            for item in assessment.edges
            if item.classification is ValidatedDependencyType.INSUFFICIENT_EVIDENCE
        ]
        if unresolved_edges:
            raise DependencyAssessmentNotApprovableError(
                "Assessment has insufficient edge evidence for: "
                + ", ".join(unresolved_edges)
            )
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("Approval requires a non-empty rationale")

    mandatory = sorted(
        {
            *(
                [assessment.structural_entry_service]
                if assessment.structural_entry_service
                else []
            ),
            *(
                item.service_name
                for item in assessment.services
                if item.classification is ValidatedDependencyType.MANDATORY
            ),
        }
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
    explicit_edge_labels = bool(assessment.edge_interventions)
    edge_labels = {
        item.edge: item.classification for item in assessment.edges
    }
    contradictory_mandatory_edges = sorted(
        edge
        for edge, classification in edge_labels.items()
        if classification is ValidatedDependencyType.MANDATORY
        and not set(edge) <= set(mandatory)
    )
    if explicit_edge_labels and contradictory_mandatory_edges:
        formatted = ", ".join(
            f"{source}->{target}"
            for source, target in contradictory_mandatory_edges
        )
        raise DependencyAssessmentNotApprovableError(
            "Mandatory edge evidence conflicts with non-mandatory service labels: "
            + formatted
        )
    retained_edge_labels = {
        edge: classification
        for edge, classification in edge_labels.items()
        if set(edge) <= retained
    }
    mandatory_edges = tuple(
        sorted(
            edge
            for edge, classification in retained_edge_labels.items()
            if classification is ValidatedDependencyType.MANDATORY
        )
    ) if explicit_edge_labels else ()
    optional_edges = tuple(
        sorted(
            edge
            for edge, classification in retained_edge_labels.items()
            if classification is ValidatedDependencyType.OPTIONAL
        )
    ) if explicit_edge_labels else ()
    supporting_edges = tuple(
        sorted(
            edge
            for edge, classification in edge_labels.items()
            if classification is ValidatedDependencyType.SUPPORTING
        )
    ) if explicit_edge_labels else ()
    approved_edge_set = set(mandatory_edges + optional_edges)
    graph = WorkflowGraph(
        workflow_id=assessment.workflow_id,
        criticality=assessment.candidate_graph.criticality,
        mandatory_services=mandatory,
        optional_services=optional,
        edges=[
            edge
            for edge in assessment.candidate_graph.edges
            if edge.source in retained and edge.target in retained
            and (
                not explicit_edge_labels
                or (edge.source, edge.target) in approved_edge_set
            )
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
        mandatory_edges=mandatory_edges,
        optional_edges=optional_edges,
        excluded_supporting_edges=supporting_edges,
    )
    return repo.create_approval(approval)


def get_validated_workflow_graph(
    workflow_id: str,
    repository: DependencyAssessmentRepository | None = None,
) -> WorkflowGraph:
    return _repository(repository).latest_approval(workflow_id).approved_graph
