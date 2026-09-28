"""auto_discovery.py — Automated Root-Span Workflow Discovery (Member 1)

Enables zero-configuration discovery of business workflows directly from
raw Jaeger distributed traces by analyzing root spans (the entry point of
a business transaction) and their downstream causal call trees.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.engine.workflow_dependency_modeling.interaction_extractor import (
    extract_interactions,
)
from app.engine.workflow_dependency_modeling.trace_reader import (
    Span,
    parse_spans,
)


@dataclass
class DiscoveredWorkflowSummary:
    """Summary of a business workflow discovered automatically from traces."""

    workflow_id: str
    """Normalized identifier (e.g. 'cart_checkout' or 'checkout')."""

    operation_name: str
    """Root operation name from the trace (e.g. 'POST /cart/checkout')."""

    root_service: str
    """Ingress / entry microservice that handled the root span (e.g. 'frontend')."""

    trace_count: int
    """Number of trace runs observed for this workflow."""

    participating_services: list[str] = field(default_factory=list)
    """All microservices that participated in this workflow across all traces."""

    sample_trace_ids: list[str] = field(default_factory=list)
    """Sample trace IDs observed for this workflow."""


def _slugify(text: str) -> str:
    """Normalize an operation name into a clean workflow ID slug."""
    clean = re.sub(r"[^\w\s-]", "_", text).strip().lower()
    clean = re.sub(r"[-\s_]+", "_", clean)
    return clean.strip("_") or "workflow"


def operation_key(root: Span) -> str | None:
    """Group requests by method and route, excluding health and telemetry traffic."""
    method = root.tags.get("http.request.method", root.tags.get("http.method"))
    target = root.tags.get("http.route") or root.tags.get("http.target") or root.tags.get("url.full")
    if method and target:
        path = urlsplit(target).path
        if path in {"/_healthz", "/healthz", "/health", "/readyz", "/metrics"} or path.startswith("/static/"):
            return None
        # Online Boutique product IDs identify resources, not different workflows.
        path = re.sub(r"^/product/[^/]+$", "/product/{id}", path)
        return f"{method.upper()} {path}"
    if "TraceService/Export" in root.operation_name or root.tags.get("span.kind") == "client":
        return None
    return root.operation_name


def select_workflow_traces(traces: list[dict], operation: str) -> list[dict]:
    selected = []
    for trace in traces:
        spans = parse_spans([trace])
        roots = find_root_spans({trace.get("traceID", ""): spans})
        if roots and operation_key(next(iter(roots.values()))) == operation:
            selected.append(trace)
    return selected


def find_root_spans(spans_by_trace: dict[str, list[Span]]) -> dict[str, Span]:
    """Find the root span for each trace in a collection.

    The root span is either:
    1. A span with ``parent_span_id is None``.
    2. If all spans have parent IDs (e.g. external gateway), the span whose
       parent ID is not present in the trace's span set.

    Args:
        spans_by_trace: Dict mapping trace_id -> list of Spans.

    Returns:
        Dict mapping trace_id -> root Span.
    """
    roots: dict[str, Span] = {}

    for trace_id, trace_spans in spans_by_trace.items():
        if not trace_spans:
            continue

        known_span_ids = {s.span_id for s in trace_spans}
        root_candidate = None

        # Pass 1: Look for explicit parent_span_id is None
        for s in trace_spans:
            if s.parent_span_id is None:
                root_candidate = s
                break

        # Pass 2: Look for parent_span_id not in this trace's span set
        if root_candidate is None:
            for s in trace_spans:
                if s.parent_span_id not in known_span_ids:
                    root_candidate = s
                    break

        # Fallback: Pick the span with the earliest startTime / duration
        if root_candidate is None:
            root_candidate = trace_spans[0]

        roots[trace_id] = root_candidate

    return roots


def discover_root_workflows(traces: list[dict[str, Any]]) -> list[DiscoveredWorkflowSummary]:
    """Scan raw Jaeger traces and auto-discover all distinct entry workflows.

    Args:
        traces: Raw Jaeger trace list (from file or Jaeger REST API).

    Returns:
        List of :class:`DiscoveredWorkflowSummary` sorted by trace count descending.
    """
    spans = parse_spans(traces)
    if not spans:
        return []

    spans_by_trace: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        spans_by_trace[s.trace_id].append(s)

    root_spans = find_root_spans(spans_by_trace)

    # Group traces by root operation name
    traces_by_operation: dict[str, list[str]] = defaultdict(list)
    root_service_by_op: dict[str, str] = {}

    for trace_id, root in root_spans.items():
        op_name = operation_key(root)
        if not op_name:
            continue
        traces_by_operation[op_name].append(trace_id)
        if op_name not in root_service_by_op:
            root_service_by_op[op_name] = root.service_name

    results: list[DiscoveredWorkflowSummary] = []

    for op_name, trace_ids in traces_by_operation.items():
        # Find all participating services for this operation
        services: set[str] = set()
        for tid in trace_ids:
            for s in spans_by_trace[tid]:
                services.add(s.service_name)

        workflow_id = _slugify(op_name)

        results.append(
            DiscoveredWorkflowSummary(
                workflow_id=workflow_id,
                operation_name=op_name,
                root_service=root_service_by_op[op_name],
                trace_count=len(trace_ids),
                participating_services=sorted(services),
                sample_trace_ids=trace_ids[:5],
            )
        )

    # Sort most frequent workflows first
    results.sort(key=lambda w: w.trace_count, reverse=True)
    return results


def build_workflow_definition_from_traces(
    traces: list[dict[str, Any]],
    operation_name: str,
    criticality: float = 0.8,
) -> dict[str, Any]:
    """Auto-generate a workflow definition dict from traces matching an operation.

    Enables zero-touch graph construction without needing any pre-existing JSON.

    Args:
        traces: Raw Jaeger trace list.
        operation_name: Target root operation (or workflow ID).
        criticality: Business criticality rating (0.0 to 1.0, default 0.8).

    Returns:
        Workflow definition dict containing ``workflowId``, ``criticality``,
        ``expectedServices``, and ``expectedEdges``.
    """
    spans = parse_spans(traces)
    spans_by_trace: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        spans_by_trace[s.trace_id].append(s)

    root_spans = find_root_spans(spans_by_trace)

    # Filter matching trace IDs
    matching_trace_ids = {
        tid
        for tid, root in root_spans.items()
        if root.operation_name == operation_name or _slugify(root.operation_name) == _slugify(operation_name)
    }

    matching_spans = [s for s in spans if s.trace_id in matching_trace_ids]
    if not matching_spans:
        # If no strict root match, use all spans
        matching_spans = spans

    interactions = extract_interactions(matching_spans)

    services: set[str] = {s.service_name for s in matching_spans}
    edges_set: set[tuple[str, str]] = set()

    for interaction in interactions:
        for edge in interaction.edges:
            services.add(edge.source)
            services.add(edge.target)
            edges_set.add((edge.source, edge.target))

    return {
        "workflowId": _slugify(operation_name),
        "criticality": criticality,
        "expectedServices": sorted(services),
        "expectedEdges": [{"source": src, "target": tgt} for src, tgt in sorted(edges_set)],
    }
