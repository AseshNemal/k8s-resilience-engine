"""Root-span candidate-operation discovery for Component 1.

Trace operations can suggest workflow candidates, but cannot supply business
meaning, a success condition, criticality, or reference truth.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

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


def slugify_operation(text: str) -> str:
    """Normalize an operation name into a clean workflow ID slug."""
    clean = re.sub(r"[^\w\s-]", "_", text).strip().lower()
    clean = re.sub(r"[-\s_]+", "_", clean)
    return clean.strip("_") or "workflow"


# Backward-compatible alias for older imports. New code uses the public name.
_slugify = slugify_operation


_DYNAMIC_PATH_SEGMENT = re.compile(
    r"^(?:\d+|[0-9a-f]{8,}|[0-9a-f]{8}-[0-9a-f-]{27,})$",
    re.IGNORECASE,
)


def _normalized_http_path(path: str) -> str:
    """Normalize only strong identifier shapes without application-name rules."""
    segments = path.split("/")
    return "/".join(
        "{id}" if _DYNAMIC_PATH_SEGMENT.fullmatch(segment) else segment
        for segment in segments
    )


def operation_key(
    root: Span, route_patterns: tuple[tuple[re.Pattern[str], str], ...] | None = None,
) -> str | None:
    """Group requests by method and route, excluding health and telemetry traffic."""
    method = root.tags.get("http.request.method", root.tags.get("http.method"))
    route = root.tags.get("http.route") or root.tags.get("url.template")
    target = (
        route
        or root.tags.get("http.target")
        or root.tags.get("url.path")
        or root.tags.get("url.full")
    )
    if method and target:
        path = urlsplit(target).path
        if path in {"/_healthz", "/healthz", "/health", "/readyz", "/metrics"} or path.startswith("/static/"):
            return None
        if route_patterns is None and not route:
            path = _normalized_http_path(path)
        elif route_patterns is not None:
            for pattern, template in route_patterns:
                path = pattern.sub(template, path)
        return f"{method.upper()} {path}"
    if "TraceService/Export" in root.operation_name or root.tags.get("span.kind") == "client":
        return None
    return root.operation_name


def select_workflow_traces(
    traces: list[dict], operation: str, entry_service: str | None = None,
    route_patterns: tuple[tuple[re.Pattern[str], str], ...] | None = None,
) -> list[dict]:
    selected = []
    for trace in traces:
        spans = parse_spans([trace])
        roots = find_root_spans({trace.get("traceID", ""): spans})
        root = next(iter(roots.values()), None)
        if root and operation in {operation_key(root, route_patterns), operation_key(root, ())} and (
            entry_service is None or root.service_name == entry_service
        ):
            selected.append(trace)
    return selected


def _find_root_span(trace_spans: list[Span]) -> Span:
    """Choose the explicit, external-parent, or earliest span for one trace."""
    known_span_ids = {span.span_id for span in trace_spans}
    explicit_root = next(
        (span for span in trace_spans if span.parent_span_id is None),
        None,
    )
    if explicit_root is not None:
        return explicit_root

    external_parent_root = next(
        (span for span in trace_spans if span.parent_span_id not in known_span_ids),
        None,
    )
    return external_parent_root or trace_spans[0]


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
    return {
        trace_id: _find_root_span(trace_spans)
        for trace_id, trace_spans in spans_by_trace.items()
        if trace_spans
    }


def discover_root_workflows(
    traces: list[dict[str, Any]],
    route_patterns: tuple[tuple[re.Pattern[str], str], ...] | None = None,
) -> list[DiscoveredWorkflowSummary]:
    """Scan raw Jaeger traces and auto-discover all distinct entry workflows.

    Args:
        traces: Raw Jaeger trace list (from file or Jaeger REST API).
        route_patterns: Route templates that merge resource IDs; ``()`` for none.

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
    traces_by_operation: dict[tuple[str, str], list[str]] = defaultdict(list)

    for trace_id, root in root_spans.items():
        op_name = operation_key(root, route_patterns)
        if not op_name:
            continue
        traces_by_operation[(root.service_name, op_name)].append(trace_id)

    results: list[DiscoveredWorkflowSummary] = []

    for (root_service, op_name), trace_ids in traces_by_operation.items():
        # Find all participating services for this operation
        services: set[str] = set()
        for tid in trace_ids:
            for s in spans_by_trace[tid]:
                services.add(s.service_name)

        workflow_id = slugify_operation(op_name)

        results.append(
            DiscoveredWorkflowSummary(
                workflow_id=workflow_id,
                operation_name=op_name,
                root_service=root_service,
                trace_count=len(trace_ids),
                participating_services=sorted(services),
                sample_trace_ids=trace_ids[:5],
            )
        )

    # Sort most frequent workflows first
    results.sort(key=lambda w: (-w.trace_count, w.root_service, w.operation_name))
    return results
