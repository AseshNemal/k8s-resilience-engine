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
# Sample-data entry point (used by /api/workflows/sample)
# ---------------------------------------------------------------------------


def get_sample_workflow() -> WorkflowGraph:
    """Return the checkout :class:`WorkflowGraph` built from mock trace data.

    Loads the checkout workflow definition and mock Jaeger traces from
    ``sample_data/`` and runs them through the real pipeline.  The
    ``/api/workflows/sample`` endpoint calls this function so the API always
    exercises the actual algorithm rather than returning a static fixture.

    Returns:
        A validated :class:`WorkflowGraph` for the ``"checkout"`` workflow.
    """
    with open(_DEFINITIONS_DIR / "checkout.json", encoding="utf-8") as fh:
        workflow_definition = json.load(fh)

    raw_traces = load_traces_from_file(_TRACES_DIR / "checkout_traces.json")

    return build_workflow_graph(workflow_definition, raw_traces)