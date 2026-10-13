"""interaction_extractor.py — Member 1: Workflow Dependency Modeling

Extracts service-to-service call edges from parsed Jaeger spans and
aggregates them across multiple trace runs to produce per-edge occurrence
statistics.

Pipeline position::

    trace_reader.parse_spans()
        -> extract_interactions()   # per-trace directed edges
        -> aggregate_edges()        # cross-trace frequency
        -> dependency_classifier    # (Part 3)

Frequency measures observed participation, not dependency necessity.
Mandatory, supporting, and optional claims require controlled outcome
assessment; even an edge present in every trace may be nonessential.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from ipaddress import ip_address

from app.engine.workflow_dependency_modeling.trace_reader import Span


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceEdge:
    """A directed service-to-service call edge observed in a trace."""

    source: str
    """Caller service name."""

    target: str
    """Callee service name."""


@dataclass
class TraceInteraction:
    """All unique cross-service edges observed within one trace run."""

    trace_id: str
    """The Jaeger traceID this interaction belongs to."""

    edges: list[ServiceEdge] = field(default_factory=list)
    """Deduplicated directed edges for this trace (sorted for stability)."""

    observed_services: list[str] = field(default_factory=list)
    """All services participating in the trace, including root-only services."""

    broken_parent_count: int = 0
    """Spans whose declared parent was absent from the trace export."""

    linked_edges: list[ServiceEdge] = field(default_factory=list)
    """Explicit span-link associations, never proof of an asynchronous call."""

    call_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    """Distinct remote call attempts per edge before per-trace deduplication."""


@dataclass
class ObservedEdge:
    """A service edge aggregated across all trace runs.

    ``frequency`` measures the share of traces containing an edge.
    It does not by itself establish mandatory or optional dependency status.
    """

    source: str
    target: str
    occurrence_count: int
    """Number of trace runs that contain this edge."""
    frequency: float
    """``occurrence_count / total_traces`` in the range ``[0.0, 1.0]``."""
    total_call_count: int = 0
    """Distinct observed remote call attempts across all traces."""
    max_calls_per_trace: int = 0
    """Largest attempt count in one trace; values above one indicate retry/loop use."""
    repeated_trace_count: int = 0
    """Number of traces containing more than one attempt for this edge."""


def _client_peer(span: Span) -> str | None:
    """Resolve a standards-based client peer without guessing from span names."""
    if span.tags.get("span.kind", "").lower() != "client":
        return None
    raw_target = span.tags.get("server.address") or span.tags.get("net.peer.name")
    if not raw_target:
        return None
    host = raw_target.strip().lower().rstrip(".")
    if host.startswith("[") and "]" in host:
        host = host[1:host.index("]")]
    elif host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit():
        host = host.rsplit(":", 1)[0]
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        # An address identifies an endpoint, not a stable logical service.
        return None
    target = host.split(".", 1)[0]
    if not target or target == "localhost":
        return None
    return target


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def extract_interactions(spans: list[Span]) -> list[TraceInteraction]:
    """Extract unique cross-service call edges from a flat list of spans.

    For each span that carries a ``parent_span_id``, the function looks up
    the parent span within the same trace. If the parent belongs to a
    *different* microservice, a directed edge is recorded:
    ``parent.service_name → span.service_name``.

    Edges are deduplicated within each trace — if service A calls service B
    multiple times in one request, it counts as a single edge for that trace.

    Args:
        spans: Flat list of :class:`~trace_reader.Span` objects, potentially
            spanning multiple traces. Typically the output of
            :func:`~trace_reader.parse_spans`.

    Returns:
        One :class:`TraceInteraction` per unique ``traceID``, sorted by
        ``trace_id`` for reproducibility. Each object contains the unique
        directed edges observed in that trace.
    """
    # Group spans by trace_id -------------------------------------------
    by_trace: dict[str, list[Span]] = defaultdict(list)
    global_span_index: dict[tuple[str, str], Span] = {}
    for span in spans:
        by_trace[span.trace_id].append(span)
        global_span_index[(span.trace_id, span.span_id)] = span

    interactions: list[TraceInteraction] = []

    for trace_id, trace_spans in by_trace.items():
        # Build a fast span_id → Span lookup for parent resolution -------
        span_index: dict[str, Span] = {s.span_id: s for s in trace_spans}

        # Collect unique (source, target) pairs as a set to deduplicate --
        edge_set: set[tuple[str, str]] = set()
        linked_edge_set: set[tuple[str, str]] = set()
        edge_call_tokens: dict[tuple[str, str], set[str]] = defaultdict(set)
        broken_parent_count = 0

        for span in trace_spans:
            peer = _client_peer(span)
            if peer is not None and peer != span.service_name.lower():
                # A client span explicitly identifies both caller (its process)
                # and callee (OpenTelemetry server.address/net.peer.name). This
                # remains valid observed-call evidence when the callee does not
                # emit a corresponding server span.
                edge_set.add((span.service_name, peer))
                edge_call_tokens[(span.service_name, peer)].add(span.span_id)

            for link in span.links:
                linked = global_span_index.get((link.trace_id, link.span_id))
                if linked is None or linked.service_name == span.service_name:
                    continue
                linked_edge_set.add((linked.service_name, span.service_name))

            if span.parent_span_id is None:
                continue  # root span — has no caller

            parent = span_index.get(span.parent_span_id)
            if parent is None:
                # Preserve the quality signal even though no edge can be
                # reconstructed without inventing a relationship.
                broken_parent_count += 1
                continue

            if parent.service_name == span.service_name:
                # Intra-service span (e.g. an internal library call within
                # the same process). Not a cross-service edge; skip.
                continue

            edge_set.add((parent.service_name, span.service_name))
            token = (
                parent.span_id
                if _client_peer(parent) == span.service_name.lower()
                else span.span_id
            )
            edge_call_tokens[(parent.service_name, span.service_name)].add(token)

        edges = [
            ServiceEdge(source=src, target=tgt)
            for src, tgt in sorted(edge_set)
        ]
        interactions.append(
            TraceInteraction(
                trace_id=trace_id,
                edges=edges,
                linked_edges=[
                    ServiceEdge(source=src, target=tgt)
                    for src, tgt in sorted(linked_edge_set)
                ],
                call_counts={
                    edge: len(tokens)
                    for edge, tokens in sorted(edge_call_tokens.items())
                },
                observed_services=sorted(
                    {
                        service
                        for span in trace_spans
                        for service in (span.service_name, _client_peer(span))
                        if service
                    }
                ),
                broken_parent_count=broken_parent_count,
            )
        )

    # Stable output — sorted by trace_id ---------------------------------
    interactions.sort(key=lambda ti: ti.trace_id)
    return interactions


def aggregate_edges(
    interactions: list[TraceInteraction],
) -> list[ObservedEdge]:
    """Count how many traces each edge appears in and compute its frequency.

    Args:
        interactions: List of :class:`TraceInteraction` objects from
            :func:`extract_interactions`.

    Returns:
        List of :class:`ObservedEdge` objects sorted by descending frequency,
        with alphabetical ``(source, target)`` as a tie-breaker. Returns an
        empty list when *interactions* is empty.
    """
    total_traces = len(interactions)
    if total_traces == 0:
        return []

    # Count occurrences per (source, target) pair -----------------------
    counts: dict[tuple[str, str], int] = defaultdict(int)
    call_counts: dict[tuple[str, str], list[int]] = defaultdict(list)
    for interaction in interactions:
        for edge in interaction.edges:
            key = (edge.source, edge.target)
            counts[key] += 1
            call_counts[key].append(interaction.call_counts.get(key, 1))

    observed: list[ObservedEdge] = [
        ObservedEdge(
            source=src,
            target=tgt,
            occurrence_count=count,
            frequency=count / total_traces,
            total_call_count=sum(call_counts[(src, tgt)]),
            max_calls_per_trace=max(call_counts[(src, tgt)], default=0),
            repeated_trace_count=sum(
                value > 1 for value in call_counts[(src, tgt)]
            ),
        )
        for (src, tgt), count in counts.items()
    ]

    # Sort: highest frequency first; alphabetical tie-break -------------
    observed.sort(key=lambda e: (-e.frequency, e.source, e.target))
    return observed


def aggregate_linked_edges(
    interactions: list[TraceInteraction],
) -> list[ObservedEdge]:
    """Aggregate explicit span-link relationships without calling them RPC edges."""
    projected = [
        TraceInteraction(trace_id=item.trace_id, edges=item.linked_edges)
        for item in interactions
    ]
    return aggregate_edges(projected)
