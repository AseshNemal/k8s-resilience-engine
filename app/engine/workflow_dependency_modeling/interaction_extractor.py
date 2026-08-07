"""interaction_extractor.py — Member 1: Workflow Dependency Modeling

Extracts service-to-service call edges from parsed Jaeger spans and
aggregates them across multiple trace runs to produce per-edge occurrence
statistics.

Pipeline position::

    trace_reader.parse_spans()
        -> extract_interactions()   # per-trace directed edges
        -> aggregate_edges()        # cross-trace frequency
        -> dependency_classifier    # (Part 3)

The key insight that makes the classifier work: a service edge whose
*frequency* across all traces equals 1.0 appeared in every execution of
the workflow, which is strong evidence that the target service is mandatory.
An edge with frequency < 1.0 was sometimes absent, suggesting the target
service is optional.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

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


@dataclass
class ObservedEdge:
    """A service edge aggregated across all trace runs.

    ``frequency`` is the primary signal consumed by the dependency
    classifier in Part 3:

    - ``frequency == 1.0`` → edge present in every trace → likely mandatory
    - ``frequency < 1.0``  → edge absent from some traces → likely optional
    """

    source: str
    target: str
    occurrence_count: int
    """Number of trace runs that contain this edge."""
    frequency: float
    """``occurrence_count / total_traces`` in the range ``[0.0, 1.0]``."""


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
    for span in spans:
        by_trace[span.trace_id].append(span)

    interactions: list[TraceInteraction] = []

    for trace_id, trace_spans in by_trace.items():
        # Build a fast span_id → Span lookup for parent resolution -------
        span_index: dict[str, Span] = {s.span_id: s for s in trace_spans}

        # Collect unique (source, target) pairs as a set to deduplicate --
        edge_set: set[tuple[str, str]] = set()

        for span in trace_spans:
            if span.parent_span_id is None:
                continue  # root span — has no caller

            parent = span_index.get(span.parent_span_id)
            if parent is None:
                # Parent span is not present in this trace export (e.g.
                # an external upstream not instrumented). Skip silently.
                continue

            if parent.service_name == span.service_name:
                # Intra-service span (e.g. an internal library call within
                # the same process). Not a cross-service edge; skip.
                continue

            edge_set.add((parent.service_name, span.service_name))

        edges = [
            ServiceEdge(source=src, target=tgt)
            for src, tgt in sorted(edge_set)
        ]
        interactions.append(TraceInteraction(trace_id=trace_id, edges=edges))

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
    for interaction in interactions:
        for edge in interaction.edges:
            counts[(edge.source, edge.target)] += 1

    observed: list[ObservedEdge] = [
        ObservedEdge(
            source=src,
            target=tgt,
            occurrence_count=count,
            frequency=count / total_traces,
        )
        for (src, tgt), count in counts.items()
    ]

    # Sort: highest frequency first; alphabetical tie-break -------------
    observed.sort(key=lambda e: (-e.frequency, e.source, e.target))
    return observed
