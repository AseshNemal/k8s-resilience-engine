"""service.py — Member 1: Workflow Dependency Modeling

Public interface for Component 1.  All other components and API routes
interact with this module only — they must not import directly from the
internal modules (``trace_reader``, ``interaction_extractor``, etc.).

Exported functions
------------------
``build_workflow_graph(workflow_definition, traces)``
    The primary research function.  Accepts a workflow definition dict and
    raw Jaeger trace data; runs the full five-stage pipeline and returns a
    validated :class:`~app.shared.schemas.workflow.WorkflowGraph`.

``get_sample_workflow()``
    Convenience entry point used by the ``/api/workflows/sample`` route.
    Runs ``build_workflow_graph`` against the mock checkout trace data so
    the API always exercises the real algorithm, never a hardcoded fixture.

Full pipeline
-------------
::

    raw Jaeger traces
        -> trace_reader.parse_spans()
        -> interaction_extractor.extract_interactions()
        -> interaction_extractor.aggregate_edges()
        -> dependency_classifier.classify_dependencies()
        -> graph_builder.build_workflow_graph()
        -> WorkflowGraph  (shared contract for Member 3)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.engine.workflow_dependency_modeling.auto_discovery import (
    build_workflow_definition_from_traces,
    discover_root_workflows,
    select_workflow_traces,
)
from app.engine.workflow_dependency_modeling.dependency_classifier import (
    classify_dependencies,
)
from app.engine.workflow_dependency_modeling.graph_builder import (
    build_workflow_graph as _assemble,
)
from app.engine.workflow_dependency_modeling.interaction_extractor import (
    aggregate_edges,
    extract_interactions,
)
from app.engine.workflow_dependency_modeling.trace_reader import (
    load_traces_from_file,
    parse_spans,
)
from app.integrations.jaeger.client import JaegerClient
from app.shared.sample_loader import SAMPLE_DATA_DIR
from app.shared.schemas.workflow import WorkflowGraph

_DEFINITIONS_DIR = SAMPLE_DATA_DIR / "workflow_definitions"
_TRACES_DIR = SAMPLE_DATA_DIR / "traces"


# ---------------------------------------------------------------------------
# Primary public interface
# ---------------------------------------------------------------------------


def build_workflow_graph(
    workflow_definition: dict,
    traces: list[dict],
    mandatory_threshold: float = 1.0,
    min_edge_frequency: float = 0.0,
) -> WorkflowGraph:
    """Build a :class:`WorkflowGraph` from a workflow definition and Jaeger traces.

    This is the **primary research function** for Component 1.  It runs
    the complete five-stage pipeline:

    1. Parse raw Jaeger span dicts into typed :class:`~trace_reader.Span`
       objects.
    2. Extract per-trace cross-service call edges.
    3. Aggregate edges across all traces to compute occurrence frequencies.
    4. Classify each service as MANDATORY, OPTIONAL, or SUPPORTING using the
       workflow definition and the configurable ``mandatory_threshold``.
    5. Assemble and return a validated :class:`WorkflowGraph`.

    Args:
        workflow_definition: Reference workflow dict loaded from
            ``sample_data/workflow_definitions/*.json`` (or from a real
            Kubernetes / registry source in future work). Must contain
            ``"workflowId"``, ``"criticality"``, ``"expectedServices"``,
            and ``"expectedEdges"``.
        traces: Raw Jaeger HTTP API trace list — the value of the ``"data"``
            key returned by :func:`~trace_reader.load_traces_from_file`.
        mandatory_threshold: Services with a trace frequency >=
            this value are classified as MANDATORY. Defaults to ``1.0``
            (must appear in every trace run). Lower to tolerate occasional
            missed traces (e.g. ``0.9`` for 90 % presence).
        min_edge_frequency: Only edges with frequency >= this value are
            included in the returned graph. Defaults to ``0.0`` (include
            all observed edges). Set to ``1.0`` for mandatory-only edges.

    Returns:
        A validated :class:`~app.shared.schemas.workflow.WorkflowGraph`
        ready to be returned by the API and consumed by Member 3.
    """
    spans = parse_spans(traces)
    interactions = extract_interactions(spans)
    observed = aggregate_edges(interactions)
    classification = classify_dependencies(
        interactions,
        workflow_definition,
        mandatory_threshold=mandatory_threshold,
    )
    return _assemble(
        classification,
        observed,
        workflow_definition,
        min_edge_frequency=min_edge_frequency,
    )


# ---------------------------------------------------------------------------
# Live Jaeger integration & Auto-Discovery
# ---------------------------------------------------------------------------


def discover_live_workflows(
    service_name: str = "frontend",
    jaeger_url: str = "http://localhost:16686",
    limit: int = 100,
    lookback: str = "1h",
) -> list[dict[str, Any]]:
    """Query a running Jaeger server and auto-discover all active workflows.

    Args:
        service_name: Ingress / entry microservice to inspect (default 'frontend').
        jaeger_url: Base URL of the Jaeger REST query service.
        limit: Max traces to fetch.
        lookback: Time lookback window.

    Returns:
        List of discovered workflow summary dicts.
    """
    client = JaegerClient(base_url=jaeger_url)
    traces = client.fetch_traces(
        service_name=service_name,
        limit=limit,
        lookback=lookback,
    )
    summaries = discover_root_workflows(traces)
    return [
        {
            "workflowId": s.workflow_id,
            "operationName": s.operation_name,
            "rootService": s.root_service,
            "traceCount": s.trace_count,
            "participatingServices": s.participating_services,
            "sampleTraceIds": s.sample_trace_ids,
        }
        for s in summaries
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
    """Fetch live traces from Jaeger and build a real-time WorkflowGraph.

    If no explicit workflow definition exists, automatically infers expected
    services and causal edges from the live traces.

    Args:
        service_name: Entry microservice (e.g. 'frontend').
        operation: Target operation / workflow (e.g. 'checkout'). If None,
            uses the most frequent discovered root operation.
        jaeger_url: Jaeger server REST endpoint.
        criticality: Business criticality rating.
        mandatory_threshold: Mandatory presence threshold (default 1.0 = 100%).
        limit: Max traces to fetch.
        lookback: Time lookback.

    Returns:
        A validated :class:`WorkflowGraph` built from live traces.
    """
    client = JaegerClient(base_url=jaeger_url)
    traces = client.fetch_traces(
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

    target_op = operation or "live_workflow"
    workflow_definition = build_workflow_definition_from_traces(
        traces,
        operation_name=target_op,
        criticality=criticality,
    )

    return build_workflow_graph(
        workflow_definition=workflow_definition,
        traces=traces,
        mandatory_threshold=mandatory_threshold,
    )


def get_live_evidence(service_name: str, operation: str, jaeger_url: str,
                      limit: int = 1000, lookback: str = "1h") -> dict[str, Any]:
    """Observed calls for one request type; trace presence is not necessity."""
    traces = JaegerClient(base_url=jaeger_url).fetch_traces(
        service_name=service_name, limit=limit, lookback=lookback)
    selected = select_workflow_traces(traces, operation)
    if not selected:
        raise ValueError("No matching traces in this window. Refresh discovery or widen the lookback.")
    spans = parse_spans(selected)
    services = sorted({span.service_name for span in spans})
    evidence = []
    for name in services:
        count = len({span.trace_id for span in spans if span.service_name == name})
        evidence.append({"service": name, "traceCount": count,
                         "frequency": count / len(selected)})
    edges = aggregate_edges(extract_interactions(spans))
    return {"workflowId": operation, "criticality": None,
            "mandatoryServices": [], "optionalServices": [],
            "observedServices": services, "serviceEvidence": evidence,
            "traceCount": len(selected), "sampleTraceIds": [t["traceID"] for t in selected[:10]],
            "sampleLimit": limit, "fetchedTraceCount": len(traces),
            "classificationStatus": "unvalidated", "source": "live",
            "edges": [{"source": e.source, "target": e.target} for e in edges]}


# ---------------------------------------------------------------------------
# Benchmark data entry points
# ---------------------------------------------------------------------------


def list_benchmark_workflows() -> list[dict[str, Any]]:
    """List all available reference benchmark workflows in the repository.

    Returns:
        List of workflow metadata dicts.
    """
    workflows = []
    if not _DEFINITIONS_DIR.exists():
        return workflows

    for path in sorted(_DEFINITIONS_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
                workflows.append({
                    "workflowId": data.get("workflowId", path.stem),
                    "description": data.get("description", ""),
                    "criticality": data.get("criticality", 0.5),
                    "expectedServices": data.get("expectedServices", []),
                    "entryService": data.get("entryService", "frontend"),
                })
        except Exception:
            continue
    return workflows


def get_workflow_definition(workflow_id: str) -> dict[str, Any]:
    """Load the raw JSON workflow definition dict for a given workflow ID.

    Args:
        workflow_id: Benchmark workflow ID.

    Returns:
        Workflow definition dictionary.

    Raises:
        FileNotFoundError: If the workflow definition JSON file does not exist.
    """
    def_file = _DEFINITIONS_DIR / f"{workflow_id}.json"
    if not def_file.exists():
        raise FileNotFoundError(f"Workflow definition '{workflow_id}' not found in {_DEFINITIONS_DIR}")

    with open(def_file, encoding="utf-8") as fh:
        return json.load(fh)


def get_workflow_by_id(
    workflow_id: str,
    mandatory_threshold: float = 1.0,
    min_edge_frequency: float = 0.0,
) -> WorkflowGraph:
    """Build a :class:`WorkflowGraph` for a specific benchmark workflow ID.

    Args:
        workflow_id: Identifier of the workflow (e.g. 'checkout', 'view_product', 'add_to_cart').
        mandatory_threshold: Minimum trace presence fraction to classify as MANDATORY.
        min_edge_frequency: Minimum edge frequency.

    Returns:
        Validated :class:`WorkflowGraph`.
    """
    workflow_definition = get_workflow_definition(workflow_id)

    trace_file_str = workflow_definition.get("traceFile")
    if trace_file_str:
        trace_path = SAMPLE_DATA_DIR.parent / trace_file_str
        if not trace_path.exists():
            trace_path = _TRACES_DIR / f"{workflow_id}_traces.json"
    else:
        trace_path = _TRACES_DIR / f"{workflow_id}_traces.json"

    if not trace_path.exists():
        # Fallback to checkout traces if specific trace file is missing
        trace_path = _TRACES_DIR / "checkout_traces.json"

    raw_traces = load_traces_from_file(trace_path)
    return build_workflow_graph(
        workflow_definition=workflow_definition,
        traces=raw_traces,
        mandatory_threshold=mandatory_threshold,
        min_edge_frequency=min_edge_frequency,
    )


def get_sample_workflow() -> WorkflowGraph:
    """Return the checkout :class:`WorkflowGraph` built from mock trace data."""
    return get_workflow_by_id("checkout")
