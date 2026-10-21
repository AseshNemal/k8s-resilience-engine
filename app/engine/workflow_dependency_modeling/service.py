"""Public service interface for Component 1 workflow dependency modeling.

Prediction consumes only a :class:`WorkflowScenario` and observed traces.
Independent reference fixtures are loaded only by evaluation endpoints.
"""

from __future__ import annotations

from time import perf_counter_ns
from typing import TYPE_CHECKING, Any, Mapping

from app.engine.workflow_dependency_modeling.analysis_session_service import (
    configure_analysis_session_repository,
    discover_analysis_workflows,
    list_analysis_services,
    get_workflow_analysis,
    list_workflow_analyses,
    start_workflow_analysis,
)
from app.engine.workflow_dependency_modeling.auto_discovery import (
    discover_root_workflows,
    select_workflow_traces,
    slugify_operation,
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
from app.engine.workflow_dependency_modeling.campaign_execution_service import (
    baseline_readiness,
    configure_intervention_event_repository,
    intervention_status,
    record_intervention_event,
    run_campaign_baseline_trial,
    run_campaign_intervention_trial,
)
from app.engine.workflow_dependency_modeling.local_boutique_intervention import (
    run_local_boutique_intervention_cohort,
)
from app.engine.workflow_dependency_modeling.campaign_assessment_service import (
    approve_experiment_campaign,
    assess_experiment_campaign,
    get_experiment_campaign_assessment,
    get_experiment_campaign_approval,
)
from app.engine.workflow_dependency_modeling.dependency_classifier import (
    classify_dependencies,
)
from app.engine.workflow_dependency_modeling.dependency_assessment_service import (
    approve_dependency_assessment,
    build_condition_scoped_workflow_graph,
    configure_dependency_assessment_repository,
    create_dependency_assessment,
    evaluate_approved_dependency_assessment,
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
from app.engine.workflow_dependency_modeling.experiment_campaign_service import (
    configure_experiment_campaign_repository,
    create_experiment_campaign,
    get_experiment_campaign,
    get_workflow_setup_requirements,
    list_experiment_campaigns,
    preview_workflow_probe,
)
from app.engine.workflow_dependency_modeling.experiment_manifest_service import (
    configure_experiment_manifest_repository,
    create_experiment_manifest,
    get_experiment_manifest,
    list_experiment_manifests,
    verify_experiment_manifest,
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
    aggregate_linked_edges,
    extract_interactions,
)
from app.engine.workflow_dependency_modeling.models import SuccessCondition, WorkflowScenario
from app.engine.workflow_dependency_modeling.offline_analysis import analyze_workflow_evidence
from app.engine.workflow_dependency_modeling.scenario_service import (
    configure_scenario_repository,
    create_scenario,
    get_versioned_scenario,
    list_versioned_scenarios,
    scenario_etag,
    transition_scenario_lifecycle,
    update_scenario,
)
from app.engine.workflow_dependency_modeling.trace_reader import parse_spans
from app.engine.workflow_dependency_modeling.trace_validator import (
    eligible_traces,
    validate_trace_quality,
)
from app.engine.workflow_dependency_modeling.graph_freeze_service import (
    configure_graph_freeze_repository,
    freeze_workflow_graph,
    get_frozen_workflow_graph,
)
from app.engine.workflow_dependency_modeling.trace_source import TraceSource
from app.engine.workflow_dependency_modeling.telemetry_readiness import (
    assess_telemetry_readiness,
    telemetry_source_capabilities,
)
from app.engine.workflow_dependency_modeling.telemetry_normalizer import (
    inspect_telemetry_payload,
    normalize_telemetry_payload,
)
from app.integrations.jaeger.client import JaegerClient
from app.shared.schemas.workflow import WorkflowGraph

if TYPE_CHECKING:
    from app.engine.workflow_dependency_modeling.evaluator import GraphEvaluationResult


def _as_scenario(value: WorkflowScenario | Mapping[str, Any]) -> WorkflowScenario:
    if isinstance(value, WorkflowScenario):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("workflowDefinition must be an object")
    return WorkflowScenario.from_mapping(value)


def build_workflow_graph(
    scenario: WorkflowScenario | Mapping[str, Any],
    traces: list[dict],
    mandatory_threshold: float = 1.0,
    min_edge_frequency: float = 0.0,
) -> WorkflowGraph:
    """Build a candidate graph without loading or accepting reference truth."""
    typed_scenario = _as_scenario(scenario)
    quality = validate_trace_quality(traces)
    valid_traces = eligible_traces(traces, quality)
    if not valid_traces:
        raise ValueError(
            f"No valid trace evidence remains for workflow '{typed_scenario.workflow_id}'; "
            f"excluded {quality.excluded_count} trace(s)"
        )
    spans = parse_spans(valid_traces)
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
    trace_source: TraceSource | None = None,
) -> list[dict[str, Any]]:
    """Discover candidate operations from observed Jaeger root spans."""
    if trace_source is None:
        traces = JaegerClient(base_url=jaeger_url).fetch_traces(
            service_name=service_name,
            limit=limit,
            lookback=lookback,
        )
    else:
        traces = trace_source.fetch_workflow_traces(
            service_name=service_name,
            operation="",
            limit=limit,
            lookback=lookback,
        )
    quality = validate_trace_quality(traces)
    safe_traces = [
        trace for trace, result in zip(traces, quality.results)
        if result.quality.value != "INVALID"
    ]
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
        for summary in discover_root_workflows(safe_traces)
        if summary.root_service == service_name
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
        limit=limit,
        lookback=lookback,
    )
    if not traces:
        raise ValueError(
            f"No traces found in Jaeger at {jaeger_url} for service '{service_name}'"
            + (f" and operation '{operation}'" if operation else "")
        )
    quality = validate_trace_quality(traces)
    traces = eligible_traces(traces, quality)
    operations = [item for item in discover_root_workflows(traces) if item.root_service == service_name]
    if operation is None and len(operations) != 1:
        raise ValueError("Select one recorded operation for the requested entry service")
    operation_name = operation or operations[0].operation_name
    traces = select_workflow_traces(traces, operation_name, service_name)
    if not traces:
        raise ValueError("No eligible traces match the requested entry service and operation")
    scenario = WorkflowScenario(
        workflow_id=slugify_operation(operation_name),
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


def get_experiment_progress(campaign_id: str) -> dict[str, Any]:
    from .campaign_assessment_service import get_experiment_progress as progress
    return progress(campaign_id)


def get_boutique_demo_execution_template() -> dict[str, Any]:
    """Opt-in benchmark template; never inferred for arbitrary applications."""
    from datetime import datetime, timezone
    from .online_boutique_workload import online_boutique_checkout_probe, DEFAULT_PRODUCT_ID

    return online_boutique_checkout_probe(
        DEFAULT_PRODUCT_ID, datetime.now(timezone.utc).year + 2
    ).to_mapping()


def get_live_evidence(
    service_name: str,
    operation: str,
    jaeger_url: str,
    limit: int = 1000,
    lookback: str = "1h",
    trace_source: TraceSource | None = None,
) -> dict[str, Any]:
    """Return observed calls for one candidate operation without necessity labels."""
    if trace_source is None:
        traces = JaegerClient(base_url=jaeger_url).fetch_traces(
            service_name=service_name,
            limit=limit,
            lookback=lookback,
        )
    else:
        traces = trace_source.fetch_workflow_traces(
            service_name=service_name,
            operation="",
            limit=limit,
            lookback=lookback,
        )
    input_quality = validate_trace_quality(traces)
    # Only parse structurally safe evidence; partial traces remain visible in
    # this observation view and are explicitly reported by traceQuality.
    safe_traces = [
        trace for trace, result in zip(traces, input_quality.results)
        if result.quality.value != "INVALID"
    ]
    selected = select_workflow_traces(safe_traces, operation, service_name)
    if not selected:
        raise ValueError(
            "No matching traces in this window. Refresh discovery or widen the lookback."
        )
    spans = parse_spans(selected)
    quality = validate_trace_quality(selected)
    interactions = extract_interactions(spans)
    services = sorted({service for item in interactions for service in item.observed_services})
    evidence = []
    for name in services:
        count = sum(name in item.observed_services for item in interactions)
        evidence.append(
            {
                "service": name,
                "traceCount": count,
                "frequency": count / len(selected),
            }
        )
    edges = aggregate_edges(interactions)
    linked_edges = aggregate_linked_edges(interactions)
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
        "traceQuality": quality.to_mapping(),
        "inputTraceQuality": input_quality.to_mapping(),
        "edges": [{"source": edge.source, "target": edge.target} for edge in edges],
        "edgeEvidence": [
            {
                "source": edge.source,
                "target": edge.target,
                "occurrenceCount": edge.occurrence_count,
                "frequency": edge.frequency,
                "totalCallCount": edge.total_call_count,
                "maxCallsPerTrace": edge.max_calls_per_trace,
                "repeatedTraceCount": edge.repeated_trace_count,
            }
            for edge in edges
        ],
        "linkedEdges": [
            {
                "source": edge.source,
                "target": edge.target,
                "evidenceType": "SPAN_LINK",
                "occurrenceCount": edge.occurrence_count,
                "frequency": edge.frequency,
            }
            for edge in linked_edges
        ],
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
    """Measure fixture graph reconstruction, then evaluate it separately."""
    from app.engine.workflow_dependency_modeling.evaluator import evaluate_graph

    # Fixture I/O and independent reference loading are not part of the
    # reconstruction algorithm's processing-time measurement.
    scenario = get_scenario(workflow_id)
    traces = load_fixture_traces(workflow_id)
    started_ns = perf_counter_ns()
    generated = build_workflow_graph(
        scenario,
        traces,
        mandatory_threshold=mandatory_threshold,
    )
    processing_time_ms = (perf_counter_ns() - started_ns) / 1_000_000
    reference = get_reference(workflow_id)
    return evaluate_graph(
        generated,
        reference,
        graph_processing_time_ms=processing_time_ms,
    )


def get_sample_workflow() -> WorkflowGraph:
    """Return the checkout synthetic-fixture candidate graph."""
    return get_workflow_by_id("checkout")


__all__ = [
    "analyze_workflow_evidence",
    "assess_telemetry_readiness",
    "assess_experiment_campaign",
    "approve_experiment_campaign",
    "build_workflow_graph",
    "baseline_readiness",
    "approve_dependency_assessment",
    "build_condition_scoped_workflow_graph",
    "configure_dependency_assessment_repository",
    "configure_analysis_session_repository",
    "configure_classification_evaluation_repository",
    "configure_execution_repository",
    "configure_experiment_campaign_repository",
    "configure_experiment_manifest_repository",
    "configure_graph_freeze_repository",
    "configure_intervention_event_repository",
    "configure_scenario_repository",
    "create_dependency_assessment",
    "evaluate_approved_dependency_assessment",
    "create_dependency_classification_evaluation",
    "create_dependency_classification_reference",
    "create_scenario",
    "create_experiment_campaign",
    "create_experiment_manifest",
    "preview_workflow_probe",
    "get_workflow_setup_requirements",
    "discover_live_workflows",
    "discover_analysis_workflows",
    "list_analysis_services",
    "evaluate_workflow_by_id",
    "freeze_workflow_graph",
    "get_evidence_source",
    "get_dependency_assessment",
    "get_dependency_classification_evaluation",
    "get_dependency_classification_reference",
    "get_execution",
    "get_experiment_campaign",
    "get_experiment_manifest",
    "get_experiment_campaign_assessment",
    "get_experiment_campaign_approval",
    "get_frozen_workflow_graph",
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
    "list_experiment_campaigns",
    "list_experiment_manifests",
    "intervention_status",
    "inspect_telemetry_payload",
    "record_intervention_event",
    "list_dependency_assessments",
    "list_dependency_classification_evaluations",
    "list_dependency_classification_references",
    "list_versioned_scenarios",
    "list_workflow_analyses",
    "normalize_telemetry_payload",
    "record_execution",
    "register_jaeger_execution",
    "run_dependency_repeatability_analysis",
    "run_dependency_sensitivity_analysis",
    "run_benchmark_batch",
    "run_campaign_baseline_trial",
    "run_campaign_intervention_trial",
    "run_local_boutique_intervention_cohort",
    "scenario_etag",
    "start_workflow_analysis",
    "transition_scenario_lifecycle",
    "telemetry_source_capabilities",
    "update_scenario",
    "verify_experiment_manifest",
]
