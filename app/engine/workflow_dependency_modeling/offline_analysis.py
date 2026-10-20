"""Input-driven analysis, isolated from fixtures, persistence and cluster I/O."""

from typing import Any, Mapping

from .analysis_models import WorkflowAnalysisRequest, WorkflowAnalysisResult
from .auto_discovery import select_workflow_traces
from .dependency_classifier import classify_dependencies
from .graph_builder import build_workflow_graph
from .interaction_extractor import aggregate_edges, extract_interactions
from .models import WorkflowScenario
from .outcome_evaluator import evaluate_trace_success
from .telemetry_normalizer import normalize_telemetry_payload
from .trace_reader import parse_spans
from .trace_validator import eligible_traces, validate_trace_quality


def analyze_workflow_evidence(
    payload: WorkflowAnalysisRequest | Mapping[str, Any],
) -> WorkflowAnalysisResult:
    request = payload if isinstance(payload, WorkflowAnalysisRequest) else WorkflowAnalysisRequest.model_validate(payload)
    # Answer labels are output, never input to an observational reconstruction.
    forbidden = {"mandatoryServices", "optionalServices", "edges", "supportingServices"} & request.workflow_definition.keys()
    if forbidden:
        raise ValueError("workflowDefinition cannot contain output labels: " + ", ".join(sorted(forbidden)))
    scenario = WorkflowScenario.from_mapping(request.workflow_definition)
    operation = request.operation_name.strip()
    if not operation:
        raise ValueError("operationName must be a non-empty string")
    try:
        traces = normalize_telemetry_payload({"sourceType": request.source_type, "payload": request.traces})["traces"]
    except (TypeError, AttributeError, KeyError, OverflowError) as err:
        raise ValueError("Malformed trace export: check the selected format and nested trace fields") from err
    if not traces:
        raise ValueError("traces must contain at least one trace")
    quality = validate_trace_quality(traces)
    eligible = eligible_traces(traces, quality)
    selected = select_workflow_traces(eligible, operation, scenario.entry_service)
    outcomes = []
    successful = []
    for trace in sorted(selected, key=lambda item: item["traceID"]):
        status, assertions, reasons = evaluate_trace_success(scenario.success_condition, trace)
        outcomes.append({
            "traceId": trace["traceID"], "status": status.value,
            "assertions": [assertion.to_mapping() for assertion in assertions],
            "reasons": list(reasons),
        })
        if status.value == "succeeded":
            successful.append(trace)

    graph = None
    service_evidence = []
    edge_evidence = []
    if successful:
        interactions = extract_interactions(parse_spans(successful))
        classification = classify_dependencies(interactions, scenario.workflow_id, request.mandatory_threshold)
        observed = aggregate_edges(interactions)
        graph = build_workflow_graph(classification, observed, scenario, request.min_edge_frequency)
        service_evidence = [{
            "service": item.service_name,
            "traceCount": item.observed_trace_count,
            "frequency": item.trace_frequency,
            "classification": item.dependency_type.value,
        } for item in classification.service_classifications]
        edge_evidence = [{
            "source": edge.source, "target": edge.target,
            "traceCount": edge.occurrence_count, "frequency": edge.frequency,
            "totalCallCount": edge.total_call_count,
            "included": edge.frequency >= request.min_edge_frequency,
        } for edge in observed]
    limitations = [
        "Frequency candidates describe participation in successful traces, not proven business necessity.",
        "Mandatory, optional and supporting dependency claims require controlled outcome interventions and review.",
        "Uploaded traces do not establish sampling coverage or independently verified execution provenance.",
    ]
    if quality.excluded_count:
        limitations.append("Invalid and partial traces were excluded; frequencies may be affected by missing evidence.")
    if not selected:
        limitations.append("No eligible trace matches the requested entry service and root operation.")
    elif not successful:
        limitations.append("No matching trace satisfies the supplied success condition; no candidate graph was produced.")
    return WorkflowAnalysisResult(
        workflow_id=scenario.workflow_id, operation_name=operation,
        classification_status="frequency_candidates" if graph else "insufficient_success_evidence",
        candidate_graph=graph, input_trace_count=len(traces), matched_trace_count=len(selected),
        unrelated_trace_count=len(eligible) - len(selected), successful_trace_count=len(successful),
        failed_trace_count=sum(item["status"] == "failed" for item in outcomes),
        indeterminate_trace_count=sum(item["status"] == "indeterminate" for item in outcomes),
        classification_trace_ids=[trace["traceID"] for trace in successful],
        service_evidence=service_evidence, edge_evidence=edge_evidence,
        trace_outcomes=outcomes, trace_quality=quality.to_mapping(), limitations=limitations,
    )
