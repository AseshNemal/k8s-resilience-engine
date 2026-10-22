"""Do real, correlated traces show the Bookinfo call path?

Bookinfo calls: ``productpage`` calls ``details`` and ``reviews``; ``reviews-v2`` calls ``ratings``. With Istio
sidecars every hop reports spans, and the spans of one request share a trace because Bookinfo forwards the trace
headers. This reads real traces from Jaeger and reports which of those calls appear *linked* (a child span whose
parent belongs to another service), never from service names alone.

It only reads. It does not send a request, create a span, or fill a gap: an edge that is not in any trace is
reported as missing. What a span's tags say about the ``reviews`` version is evidence, not proof, and is reported
as such (``unknown`` when no tag says).

Only spans' service names, parent links and tag values are used; nothing about how a trace was produced is assumed
beyond Jaeger's own JSON shape.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from app.integrations.tracing.applications import jaeger_names_for, valid_name
from app.integrations.tracing.status import LOOKBACK, TraceSource, _iso

EXPECTED_EDGES: tuple[tuple[str, str], ...] = (
    ("productpage", "details"),
    ("productpage", "reviews"),
    ("reviews", "ratings"),
)
ENTRY_EDGE = ("trace-demo-client", "productpage")
MAX_TRACES = 100


class PathVerdict(str, Enum):
    UNREACHABLE = "UNREACHABLE"  # the trace service did not answer
    NO_TRACES = "NO_TRACES"  # no productpage traces to examine
    PARTIAL = "PARTIAL"  # traces exist, but an expected call is missing or reviews-v1 was seen
    VERIFIED = "VERIFIED"  # at least one trace holds every expected call, and no reviews-v1 span was seen


def short_name(service: str, namespace: str) -> str:
    """``productpage.bookinfo`` and ``productpage.bookinfo.svc.cluster.local`` both become ``productpage``."""
    name = service.lower()
    for suffix in (f".{namespace}.svc.cluster.local", f".{namespace}.svc", f".{namespace}"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def trace_edges(trace: dict[str, Any], namespace: str) -> set[tuple[str, str]]:
    """The (parent service, child service) pairs of a trace, from its CHILD_OF references. A span whose parent is in
    the same service is not an edge, and a reference to a span that is not in the trace is skipped, not guessed."""
    processes = trace.get("processes") or {}
    spans = {span["spanID"]: span for span in trace.get("spans") or [] if isinstance(span, dict) and "spanID" in span}

    def service_of(span: dict[str, Any]) -> str | None:
        name = (processes.get(span.get("processID")) or {}).get("serviceName")
        return short_name(name, namespace) if isinstance(name, str) and name else None

    edges: set[tuple[str, str]] = set()
    for span in spans.values():
        child = service_of(span)
        for reference in span.get("references") or []:
            if reference.get("refType") != "CHILD_OF":
                continue
            parent_span = spans.get(reference.get("spanID"))
            parent = service_of(parent_span) if parent_span else None
            if child and parent and child != parent:
                edges.add((parent, child))
    return edges


def reviews_version_evidence(trace: dict[str, Any], namespace: str) -> str:
    """``v2``, ``v1`` or ``unknown``, from the tag values of the spans that belong to ``reviews``. The tag names
    Istio uses are not assumed: any tag value naming ``reviews-v2`` or ``|v2|`` (an Envoy cluster name) counts."""
    processes = trace.get("processes") or {}
    seen: set[str] = set()
    for span in trace.get("spans") or []:
        service = (processes.get(span.get("processID")) or {}).get("serviceName")
        if not isinstance(service, str) or short_name(service, namespace) != "reviews":
            continue
        for tag in span.get("tags") or []:
            value = tag.get("value")
            if not isinstance(value, str):
                continue
            lowered = value.lower()
            if "reviews-v2" in lowered or "|v2|" in lowered:
                seen.add("v2")
            if "reviews-v1" in lowered or "|v1|" in lowered:
                seen.add("v1")
    if "v1" in seen:
        return "v1"  # any sign of v1 outranks v2: determinism is what is being checked
    return "v2" if "v2" in seen else "unknown"


def verify_trace_path(
    source: TraceSource,
    namespace: str = "bookinfo",
    *,
    limit: int = MAX_TRACES,
    lookback: str = LOOKBACK,
    now: float | None = None,
) -> dict[str, Any]:
    """Read real productpage traces and report which expected calls are linked in them."""
    if not valid_name(namespace):
        raise ValueError("namespace must be a Kubernetes name")
    observed = time.time() if now is None else now
    answer: dict[str, Any] = {
        "namespace": namespace,
        "verdict": PathVerdict.NO_TRACES.value,
        "tracesExamined": 0,
        "edges": [],
        "missingEdges": [f"{a}->{b}" for a, b in EXPECTED_EDGES],
        "completeTraces": 0,
        "entryTraces": 0,
        "reviewsVersion": {"v2": 0, "v1": 0, "unknown": 0},
        "observedAt": _iso(observed),
    }
    try:
        names = jaeger_names_for("productpage", namespace, source.get_services())
        traces: list[dict[str, Any]] = []
        for name in names:
            traces.extend(source.fetch_traces(name, limit=min(limit, MAX_TRACES), lookback=lookback))
    except ConnectionError:
        answer["verdict"] = PathVerdict.UNREACHABLE.value
        return answer
    if not traces:
        return answer

    counts: dict[tuple[str, str], int] = {}
    complete = entry = 0
    for trace in traces:
        edges = trace_edges(trace, namespace)
        for edge in edges:
            counts[edge] = counts.get(edge, 0) + 1
        if all(edge in edges for edge in EXPECTED_EDGES):
            complete += 1
        if ENTRY_EDGE in edges:
            entry += 1
        answer["reviewsVersion"][reviews_version_evidence(trace, namespace)] += 1

    answer["tracesExamined"] = len(traces)
    answer["completeTraces"], answer["entryTraces"] = complete, entry
    answer["edges"] = [{"from": a, "to": b, "traces": n} for (a, b), n in sorted(counts.items())]
    answer["missingEdges"] = [f"{a}->{b}" for a, b in EXPECTED_EDGES if (a, b) not in counts]
    verified = complete > 0 and answer["reviewsVersion"]["v1"] == 0
    answer["verdict"] = (PathVerdict.VERIFIED if verified else PathVerdict.PARTIAL).value
    return answer
