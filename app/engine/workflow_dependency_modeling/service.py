"""Public service interface for Component 1 workflow dependency modeling.

Prediction consumes only a :class:`WorkflowScenario` and observed traces.
Independent reference fixtures are loaded only by evaluation endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from app.engine.workflow_dependency_modeling.analysis_session_service import (
    configure_analysis_session_repository,
    get_workflow_analysis,
    list_workflow_analyses,
    start_workflow_analysis,
)
from app.engine.workflow_dependency_modeling.auto_discovery import (
    _slugify,
    discover_root_workflows,
    select_workflow_traces,
)
from app.engine.workflow_dependency_modeling.classification_evaluation_service import (
    configure_classification_evaluation_repository,
    create_dependency_classification_evaluation,
    create_dependency_classification_reference,
    get_dependency_classification_evaluation,
    get_dependency_classification_reference,
    list_dependency_classification_evaluations,
    list_dependency_classification_references,
)
from app.engine.workflow_dependency_modeling.classification_experiments import (
    run_dependency_repeatability_analysis,
    run_dependency_sensitivity_analysis,
)
from app.engine.workflow_dependency_modeling.dependency_classifier import (
    classify_dependencies,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_service import (
    approve_dependency_assessment,
    configure_dependency_assessment_repository,
    create_dependency_assessment,
    get_dependency_assessment,
    get_validated_workflow_graph,
    list_dependency_assessments,
)
from app.engine.workflow_dependency_modeling.execution_service import (
    configure_execution_repository,
    get_execution,
    list_executions,
    record_execution,
    register_jaeger_execution,
    run_benchmark_batch,
)
from app.engine.workflow_dependency_modeling.fixture_repository import (
    get_evidence_source,
    get_reference,
    get_scenario,
    list_fixture_workflows,
    load_fixture_traces,
)
from app.engine.workflow_dependency_modeling.graph_builder import (
    build_workflow_graph as _assemble,
)
from app.engine.workflow_dependency_modeling.interaction_extractor import (
    aggregate_edges,
    extract_interactions,
)
from app.engine.workflow_dependency_modeling.models import SuccessCondition, WorkflowScenario
from app.engine.workflow_dependency_modeling.scenario_service import (
    configure_scenario_repository,
    create_scenario,
    get_versioned_scenario,
    list_versioned_scenarios,
    scenario_etag,
    update_scenario,
)
from app.engine.workflow_dependency_modeling.trace_reader import parse_spans
from app.integrations.jaeger.client import JaegerClient
from app.shared.schemas.workflow import WorkflowGraph

if TYPE_CHECKING:
    from app.engine.workflow_dependency_modeling.evaluator import GraphEvaluationResult


def _as_scenario(value: WorkflowScenario | Mapping[str, Any]) -> WorkflowScenario:
    if isinstance(value, WorkflowScenario):
        return value
    return WorkflowScenario.from_mapping(value)


def build_workflow_graph(
    scenario: WorkflowScenario | Mapping[str, Any],
    traces: list[dict],
    mandatory_threshold: float = 1.0,
    min_edge_frequency: float = 0.0,
) -> WorkflowGraph:
    """Build a candidate graph without loading or accepting reference truth."""
    typed_scenario = _as_scenario(scenario)
    spans = parse_spans(traces)
    interactions = extract_interactions(spans)
    observed = aggregate_edges(interactions)
    classification = classify_dependencies(
        interactions,
        typed_scenario.workflow_id,
        mandatory_threshold=mandatory_threshold,
    )
    return _assemble(
        classification,
        observed,
        typed_scenario,
        min_edge_frequency=min_edge_frequency,
    )


def discover_live_workflows(
    service_name: str = "frontend",
    jaeger_url: str = "http://localhost:16686",
    limit: int = 100,
    lookback: str = "1h",
) -> list[dict[str, Any]]:
    """Discover candidate operations from observed Jaeger root spans."""
    traces = JaegerClient(base_url=jaeger_url).fetch_traces(
        service_name=service_name,
        limit=limit,
        lookback=lookback,
    )
    return [
        {
            "workflowId": summary.workflow_id,
            "operationName": summary.operation_name,
            "rootService": summary.root_service,
            "traceCount": summary.trace_count,
            "participatingServices": summary.participating_services,
            "sampleTraceIds": summary.sample_trace_ids,
            "status": "candidate",
        }
        for summary in discover_root_workflows(traces)
    ]


def get_live_workflow(
    service_name: str = "frontend",
    operation: str | None = None,
    jaeger_url: str = "http://localhost:16686",
    criticality: float = 0.8,
    mandatory_threshold: float = 1.0,
    limit: int = 100,
    lookback: str = "1h",
) -> WorkflowGraph:
    """Legacy candidate-graph adapter for live traces.

    The result is not evaluated or frozen. The UI uses ``get_live_evidence`` so
    it can show observed evidence without presenting candidate labels as truth.
    """
    traces = JaegerClient(base_url=jaeger_url).fetch_traces(
        service_name=service_name,
        operation=operation,
        limit=limit,
        lookback=lookback,
    )
    if not traces:
        raise ValueError(
            f"No traces found in Jaeger at {jaeger_url} for service '{service_name}'"
            + (f" and operation '{operation}'" if operation else "")
        )
    operation_name = operation or "live_workflow"
    scenario = WorkflowScenario(
        workflow_id=_slugify(operation_name),
        description="Candidate operation discovered from live trace evidence.",
        criticality=criticality,
        success_condition=SuccessCondition.from_mapping(
            {
                "protocol": "span",
                "terminalService": service_name,
                "operationName": operation_name,
                "spanStatusCodes": ["UNSET", "OK"],
            }
        ),
        entry_service=service_name,
    )
    return build_workflow_graph(
        scenario,
        traces,
        mandatory_threshold=mandatory_threshold,
    )


def get_live_evidence(
    service_name: str,
    operation: str,
    jaeger_url: str,
    limit: int = 1000,
    lookback: str = "1h",
) -> dict[str, Any]:
    """Return observed calls for one candidate operation without necessity labels."""
    traces = JaegerClient(base_url=jaeger_url).fetch_traces(
        service_name=service_name,
        limit=limit,
        lookback=lookback,
    )
    selected = select_workflow_traces(traces, operation)
    if not selected:
        raise ValueError(
            "No matching traces in this window. Refresh discovery or widen the lookback."
        )
    spans = parse_spans(selected)
    services = sorted({span.service_name for span in spans})
    evidence = []
    for name in services:
        count = len({span.trace_id for span in spans if span.service_name == name})
        evidence.append(
            {
                "service": name,
                "traceCount": count,
                "frequency": count / len(selected),
            }
        )
    edges = aggregate_edges(extract_interactions(spans))
    return {
        "workflowId": operation,
        "criticality": None,
        "mandatoryServices": [],
        "optionalServices": [],
        "observedServices": services,
        "serviceEvidence": evidence,
        "traceCount": len(selected),
        "sampleTraceIds": [trace["traceID"] for trace in selected[:10]],
        "sampleLimit": limit,
        "fetchedTraceCount": len(traces),
        "classificationStatus": "not_classified",
        "evaluationStatus": "not_evaluated",
        "source": "live_unverified",
        "edges": [{"source": edge.source, "target": edge.target} for edge in edges],
    }


def list_benchmark_workflows() -> list[dict[str, Any]]:
    """List separated synthetic fixture datasets (legacy API name)."""
    return list_fixture_workflows()


def get_workflow_by_id(
    workflow_id: str,
    mandatory_threshold: float = 1.0,
    min_edge_frequency: float = 0.0,
) -> WorkflowGraph:
    """Build a fixture prediction from scenario plus evidence only."""
    scenario = get_scenario(workflow_id)
    traces = load_fixture_traces(workflow_id)
    return build_workflow_graph(
        scenario,
        traces,
        mandatory_threshold=mandatory_threshold,
        min_edge_frequency=min_edge_frequency,
    )


def evaluate_workflow_by_id(
    workflow_id: str,
    mandatory_threshold: float = 1.0,
) -> GraphEvaluationResult:
    """Generate a fixture prediction first, then evaluate it separately."""
    from app.engine.workflow_dependency_modeling.evaluator import evaluate_graph

    generated = get_workflow_by_id(
        workflow_id, mandatory_threshold=mandatory_threshold
    )
    reference = get_reference(workflow_id)
    return evaluate_graph(generated, reference)


def get_sample_workflow() -> WorkflowGraph:
    """Return the checkout synthetic-fixture candidate graph."""
    return get_workflow_by_id("checkout")


__all__ = [
    "build_workflow_graph",
    "approve_dependency_assessment",
    "configure_dependency_assessment_repository",
    "configure_analysis_session_repository",
    "configure_classification_evaluation_repository",
    "configure_execution_repository",
    "configure_scenario_repository",
    "create_dependency_assessment",
    "create_dependency_classification_evaluation",
    "create_dependency_classification_reference",
    "create_scenario",
    "discover_live_workflows",
    "evaluate_workflow_by_id",
    "get_evidence_source",
    "get_dependency_assessment",
    "get_dependency_classification_evaluation",
    "get_dependency_classification_reference",
    "get_execution",
    "get_live_evidence",
    "get_live_workflow",
    "get_sample_workflow",
    "get_scenario",
    "get_versioned_scenario",
    "get_validated_workflow_graph",
    "get_workflow_analysis",
    "get_workflow_by_id",
    "list_benchmark_workflows",
    "list_executions",
    "list_dependency_assessments",
    "list_dependency_classification_evaluations",
    "list_dependency_classification_references",
    "list_versioned_scenarios",
    "list_workflow_analyses",
    "record_execution",
    "register_jaeger_execution",
    "run_dependency_repeatability_analysis",
    "run_dependency_sensitivity_analysis",
    "run_benchmark_batch",
    "scenario_etag",
    "start_workflow_analysis",
    "update_scenario",
]
