"""Application service for evidence-backed dependency validation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping

from app.engine.workflow_dependency_modeling.dependency_assessment_models import (
    AssessmentApproval,
    AssessmentPolicy,
    CohortStatistics,
    DependencyAssessment,
    DependencyConditionResult,
    EdgeDependencyAssessment,
    EdgeInterventionCohort,
    InterventionCohort,
    ServiceDependencyAssessment,
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
from app.engine.workflow_dependency_modeling.fixture_repository import get_reference
from app.engine.workflow_dependency_modeling.scenario_service import (
    get_versioned_scenario,
)
from app.shared.schemas.workflow import WorkflowGraph

if TYPE_CHECKING:
    from app.engine.workflow_dependency_modeling.evaluator import GraphEvaluationResult


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


def _validate_condition_records(
    records: list[ExecutionOutcome], condition_id: str
) -> None:
    mismatched = [
        item.registration.execution_id
        for item in records
        if item.registration.condition_id != condition_id
    ]
    if mismatched:
        raise ValueError(
            f"Execution cohort for condition '{condition_id}' contains mismatched executions: "
            + ", ".join(sorted(mismatched))
        )


def _combine_statistics(items: list[CohortStatistics]) -> CohortStatistics:
    eligible = sum(item.eligible_records for item in items)
    succeeded = sum(item.succeeded_records for item in items)
    return CohortStatistics(
        total_records=sum(item.total_records for item in items),
        eligible_records=eligible,
        excluded_records=sum(item.excluded_records for item in items),
        succeeded_records=succeeded,
        failed_records=sum(item.failed_records for item in items),
        success_rate=succeeded / eligible if eligible else None,
        eligible_execution_ids=tuple(
            execution_id
            for item in items
            for execution_id in item.eligible_execution_ids
        ),
        eligible_trace_ids=tuple(
            trace_id for item in items for trace_id in item.eligible_trace_ids
        ),
    )


def _condition_result(
    condition_id: str,
    assessment: ServiceDependencyAssessment | EdgeDependencyAssessment,
) -> DependencyConditionResult:
    return DependencyConditionResult(
        condition_id=condition_id,
        classification=assessment.classification,
        baseline=assessment.baseline,
        intervention=assessment.intervention,
        intervention_type=assessment.intervention_type,
        failure_effect=assessment.failure_effect,
        evidence_strength=assessment.evidence_strength,
        reasons=assessment.reasons,
        excluded_execution_ids=assessment.excluded_execution_ids,
    )


def _aggregate_classification(
    results: list[DependencyConditionResult],
) -> ValidatedDependencyType:
    labels = {item.classification for item in results}
    if ValidatedDependencyType.INSUFFICIENT_EVIDENCE in labels:
        return ValidatedDependencyType.INSUFFICIENT_EVIDENCE
    if len(labels) == 1:
        return next(iter(labels))
    return ValidatedDependencyType.CONDITIONAL


def _aggregate_service(
    service_name: str, results: list[DependencyConditionResult]
) -> ServiceDependencyAssessment:
    classification = _aggregate_classification(results)
    return ServiceDependencyAssessment(
        service_name=service_name,
        classification=classification,
        baseline=_combine_statistics([item.baseline for item in results]),
        intervention=_combine_statistics([item.intervention for item in results]),
        intervention_type=(
            results[0].intervention_type
            if len({item.intervention_type for item in results}) == 1
            else None
        ),
        failure_effect=results[0].failure_effect if len(results) == 1 else None,
        evidence_strength=min(item.evidence_strength for item in results),
        reasons=(
            ("Dependency classification differs across declared workflow conditions",)
            if classification is ValidatedDependencyType.CONDITIONAL
            else tuple(reason for item in results for reason in item.reasons)
        ),
        excluded_execution_ids=tuple(
            execution_id
            for item in results
            for execution_id in item.excluded_execution_ids
        ),
        condition_results=tuple(results),
    )


def _aggregate_edge(
    source: str, target: str, results: list[DependencyConditionResult]
) -> EdgeDependencyAssessment:
    classification = _aggregate_classification(results)
    return EdgeDependencyAssessment(
        source_service=source,
        target_service=target,
        classification=classification,
        baseline=_combine_statistics([item.baseline for item in results]),
        intervention=_combine_statistics([item.intervention for item in results]),
        intervention_type=(
            results[0].intervention_type
            if len({item.intervention_type for item in results}) == 1
            else None
        ),
        failure_effect=results[0].failure_effect if len(results) == 1 else None,
        evidence_strength=min(item.evidence_strength for item in results),
        reasons=(
            ("Dependency classification differs across declared workflow conditions",)
            if classification is ValidatedDependencyType.CONDITIONAL
            else tuple(reason for item in results for reason in item.reasons)
        ),
        excluded_execution_ids=tuple(
            execution_id
            for item in results
            for execution_id in item.excluded_execution_ids
        ),
        condition_results=tuple(results),
    )


def _assessment_scope(
    graph: WorkflowGraph,
    baseline_ids: tuple[str, ...],
    interventions: tuple[InterventionCohort, ...],
    edge_interventions: tuple[EdgeInterventionCohort, ...],
    structural_entry_service: str | None,
) -> set[str]:
    intervention_services = [item.target_service for item in interventions]
    service_condition_keys = [
        (item.target_service, item.condition_id) for item in interventions
    ]
    if len(service_condition_keys) != len(set(service_condition_keys)):
        raise ValueError(
            "Only one intervention cohort is allowed per candidate service and condition"
        )
    edge_keys = [
        (item.source_service, item.target_service, item.condition_id)
        for item in edge_interventions
    ]
    if len(edge_keys) != len(set(edge_keys)):
        raise ValueError(
            "Only one intervention cohort is allowed per candidate edge and condition"
        )
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
    unknown_edges = {(source, target) for source, target, _ in edge_keys} - candidate_edges
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
    scenario = get_versioned_scenario(workflow_id, scenario_version)
    condition_ids = tuple(item.condition_id for item in scenario.conditions)

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
    undeclared_conditions = {
        item.registration.condition_id for item in baseline_records
    } - set(condition_ids)
    undeclared_conditions |= {
        item.condition_id for item in (*interventions, *edge_interventions)
    } - set(condition_ids)
    if undeclared_conditions:
        raise ValueError(
            "Assessment evidence uses undeclared workflow conditions: "
            + ", ".join(sorted(undeclared_conditions))
        )
    policy = AssessmentPolicy.from_mapping(data.get("policy"))
    service_results = []
    for service_name in sorted(candidate_services - {structural_entry_service}):
        condition_results = []
        for condition_id in condition_ids:
            intervention = next(
                (
                    item for item in interventions
                    if item.target_service == service_name
                    and item.condition_id == condition_id
                ),
                None,
            )
            records = (
                _records_for_batches(intervention.batch_ids, execution_repository)
                if intervention
                else []
            )
            _validate_records(records, workflow_id, scenario_version, environment_id)
            _validate_condition_records(records, condition_id)
            baseline_for_condition = [
                item for item in baseline_records
                if item.registration.condition_id == condition_id
            ]
            result = assess_service_dependency(
                service_name,
                baseline_for_condition,
                intervention,
                records,
                policy,
            )
            condition_results.append(_condition_result(condition_id, result))
        service_results.append(_aggregate_service(service_name, condition_results))

    edge_results = []
    for edge in sorted(graph.edges, key=lambda item: (item.source, item.target)):
        condition_results = []
        for condition_id in condition_ids:
            intervention = next(
                (
                    item
                    for item in edge_interventions
                    if (item.source_service, item.target_service, item.condition_id)
                    == (edge.source, edge.target, condition_id)
                ),
                None,
            )
            records = (
                _records_for_batches(intervention.batch_ids, execution_repository)
                if intervention
                else []
            )
            _validate_records(records, workflow_id, scenario_version, environment_id)
            _validate_condition_records(records, condition_id)
            baseline_for_condition = [
                item for item in baseline_records
                if item.registration.condition_id == condition_id
            ]
            result = assess_edge_dependency(
                edge.source,
                edge.target,
                baseline_for_condition,
                intervention,
                records,
                policy,
            )
            condition_results.append(_condition_result(condition_id, result))
        edge_results.append(_aggregate_edge(edge.source, edge.target, condition_results))

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
    conditional = [
        item.service_name
        for item in assessment.services
        if item.classification is ValidatedDependencyType.CONDITIONAL
    ]
    if conditional:
        raise DependencyAssessmentNotApprovableError(
            "Conditional dependencies require a condition-scoped graph export: "
            + ", ".join(conditional)
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
        conditional_edges = [
            f"{item.source_service}->{item.target_service}"
            for item in assessment.edges
            if item.classification is ValidatedDependencyType.CONDITIONAL
        ]
        if conditional_edges:
            raise DependencyAssessmentNotApprovableError(
                "Conditional edge dependencies require a condition-scoped graph export: "
                + ", ".join(conditional_edges)
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


def build_condition_scoped_workflow_graph(
    assessment_id: str,
    condition_id: str,
    repository: DependencyAssessmentRepository | None = None,
) -> WorkflowGraph:
    """Project one declared condition without weakening the shared graph contract.

    The caller receives an ordinary ``WorkflowGraph`` whose meaning is explicitly
    scoped by the requested condition. It is not persisted as an approved graph.
    """
    assessment = _repository(repository).get_assessment(assessment_id)

    def service_result(item: ServiceDependencyAssessment) -> DependencyConditionResult:
        try:
            return next(
                result for result in item.condition_results
                if result.condition_id == condition_id
            )
        except StopIteration as err:
            raise ValueError(
                f"Assessment has no service evidence for condition '{condition_id}'"
            ) from err

    def edge_result(item: EdgeDependencyAssessment) -> DependencyConditionResult:
        try:
            return next(
                result for result in item.condition_results
                if result.condition_id == condition_id
            )
        except StopIteration as err:
            raise ValueError(
                f"Assessment has no edge evidence for condition '{condition_id}'"
            ) from err

    service_results = [(item, service_result(item)) for item in assessment.services]
    if not service_results and condition_id != "default":
        raise ValueError("Assessment has no evidence for the requested condition")
    unresolved = [
        item.service_name
        for item, result in service_results
        if result.classification in {ValidatedDependencyType.INSUFFICIENT_EVIDENCE, ValidatedDependencyType.CONDITIONAL}
    ]
    if unresolved:
        raise DependencyAssessmentNotApprovableError(
            f"Condition '{condition_id}' has insufficient evidence for: "
            + ", ".join(unresolved)
        )
    mandatory = sorted(
        {
            *(
                [assessment.structural_entry_service]
                if assessment.structural_entry_service
                else []
            ),
            *(
                item.service_name
                for item, result in service_results
                if result.classification is ValidatedDependencyType.MANDATORY
            ),
        }
    )
    optional = sorted(
        item.service_name
        for item, result in service_results
        if result.classification is ValidatedDependencyType.OPTIONAL
    )
    retained = set(mandatory + optional)
    explicit_edge_labels = bool(assessment.edge_interventions)
    retained_edges: set[tuple[str, str]] = set()
    if explicit_edge_labels:
        edge_results = [(item, edge_result(item)) for item in assessment.edges]
        unresolved_edges = [
            f"{item.source_service}->{item.target_service}"
            for item, result in edge_results
            if result.classification in {ValidatedDependencyType.INSUFFICIENT_EVIDENCE, ValidatedDependencyType.CONDITIONAL}
        ]
        if unresolved_edges:
            raise DependencyAssessmentNotApprovableError(
                f"Condition '{condition_id}' has insufficient edge evidence for: "
                + ", ".join(unresolved_edges)
            )
        if any(result.classification is ValidatedDependencyType.MANDATORY and not set(item.edge) <= set(mandatory) for item, result in edge_results):
            raise DependencyAssessmentNotApprovableError("Mandatory edge evidence conflicts with condition-specific service labels")
        retained_edges = {
            item.edge
            for item, result in edge_results
            if result.classification
            in {ValidatedDependencyType.MANDATORY, ValidatedDependencyType.OPTIONAL}
        }
    return WorkflowGraph(
        workflow_id=assessment.workflow_id,
        criticality=assessment.candidate_graph.criticality,
        mandatory_services=mandatory,
        optional_services=optional,
        edges=[
            edge
            for edge in assessment.candidate_graph.edges
            if edge.source in retained
            and edge.target in retained
            and (
                not explicit_edge_labels
                or (edge.source, edge.target) in retained_edges
            )
        ],
    )


def evaluate_approved_dependency_assessment(
    assessment_id: str,
    repository: DependencyAssessmentRepository | None = None,
) -> GraphEvaluationResult:
    """Score one approved prediction against its independent reference graph."""
    from app.engine.workflow_dependency_modeling.evaluator import evaluate_graph

    repo = _repository(repository)
    assessment = repo.get_assessment(assessment_id)
    approval = repo.get_approval(assessment_id)
    reference = get_reference(assessment.workflow_id)
    predicted_mandatory_edges = (
        set(approval.mandatory_edges) if assessment.edge_interventions else None
    )
    return evaluate_graph(
        approval.approved_graph,
        reference,
        predicted_mandatory_edges=predicted_mandatory_edges,
    )
