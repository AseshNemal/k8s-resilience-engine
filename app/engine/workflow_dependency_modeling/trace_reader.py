"""trace_reader.py — Member 1: Workflow Dependency Modeling

Responsible for loading raw Jaeger-export trace JSON from disk and converting
it into typed ``Span`` objects for downstream processing.

The Jaeger HTTP API export format used here is::

    {
      "data": [
        {
          "traceID": "...",
          "spans": [
            {
              "traceID": "...",
              "spanID": "...",
              "operationName": "...",
              "references": [
                {"refType": "CHILD_OF", "traceID": "...", "spanID": "..."}
              ],
              "startTime": 1700000000000000,   # microseconds epoch
              "duration": 245000,              # microseconds
              "tags": [
                {"key": "error", "type": "bool", "value": false},
                ...
              ],
              "processID": "p1"
            }
          ],
          "processes": {
            "p1": {"serviceName": "frontend", "tags": []},
            ...
          }
        }
      ]
    }

``parse_spans`` resolves each span's ``processID`` to the process map to get the
real ``serviceName`` and resolves parent-child relationships from the
``references`` list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanLink:
    """A non-parent causal reference, such as OTel link or FOLLOWS_FROM."""

    trace_id: str
    span_id: str
    reference_type: str


@dataclass
class Span:
    """A single OpenTelemetry / Jaeger span, normalised from the raw export."""

    trace_id: str
    """Identifier shared by all spans belonging to one end-to-end request."""

    span_id: str
    """Unique identifier for this individual span."""

    parent_span_id: str | None
    """``spanID`` of the parent span, or ``None`` for a root span."""

    service_name: str
    """Microservice that emitted this span (resolved from the process map)."""

    operation_name: str
    """Name of the operation (e.g. ``"payment.charge"``)."""

    duration_us: int
    """Span duration in microseconds."""

    tags: dict[str, str] = field(default_factory=dict)
    """Flat key->value tag map (all values normalised to ``str``)."""

    error: bool = False
    """``True`` when the span's ``error`` tag is set to ``True``."""

    links: tuple[SpanLink, ...] = ()
    """Explicit non-parent references kept separate from synchronous calls."""

    start_time_us: int | None = None
    """Original epoch microseconds, absent when the exporter did not provide them."""


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def load_traces_from_file(path: str | Path) -> list[dict]:
    """Load raw Jaeger-export JSON from *path* and return the trace list.

    The function handles two common shapes:

    * A dict with a top-level ``"data"`` key (standard Jaeger HTTP API export).
    * A bare list of trace dicts.

    Args:
        path: Absolute or relative path to the ``.json`` trace file.

    Returns:
        A list of raw trace dicts, each containing ``"spans"`` and
        ``"processes"`` keys.

    Raises:
        FileNotFoundError: If *path* does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)

    if isinstance(payload, dict):
        return payload.get("data", [])

    # Bare list format
    return list(payload)


def _parent_span_id(raw_span: dict) -> str | None:
    """Return the first non-empty CHILD_OF parent span ID."""
    child_reference = next(
        (
            reference
            for reference in raw_span.get("references", [])
            if reference.get("refType") == "CHILD_OF"
        ),
        None,
    )
    if child_reference is None:
        return None
    return child_reference.get("spanID") or None


def _span_links(raw_span: dict, trace_id: str) -> tuple[SpanLink, ...]:
    links: list[SpanLink] = []
    for reference in raw_span.get("references", []):
        reference_type = str(reference.get("refType", "")).upper()
        span_id = str(reference.get("spanID", "")).strip()
        if reference_type == "CHILD_OF" or not span_id:
            continue
        links.append(
            SpanLink(
                trace_id=str(reference.get("traceID") or trace_id),
                span_id=span_id,
                reference_type=reference_type or "SPAN_LINK",
            )
        )
    return tuple(links)


def _span_tags(raw_span: dict) -> tuple[dict[str, str], bool]:
    """Flatten Jaeger tags and report whether the error tag is true."""
    raw_tags = raw_span.get("tags", [])
    tags = {tag.get("key", ""): str(tag.get("value")) for tag in raw_tags}
    has_error = any(
        tag.get("key") == "error" and tag.get("value") is True
        for tag in raw_tags
    )
    return tags, has_error


def _parse_span(raw_span: dict, trace_id: str, processes: dict[str, dict]) -> Span:
    """Convert one raw Jaeger span using its trace-level process map."""
    process = processes.get(raw_span.get("processID", ""), {})
    tags, has_error = _span_tags(raw_span)
    return Span(
        trace_id=raw_span.get("traceID", trace_id),
        span_id=raw_span.get("spanID", ""),
        parent_span_id=_parent_span_id(raw_span),
        service_name=process.get("serviceName", "unknown"),
        operation_name=raw_span.get("operationName", ""),
        duration_us=raw_span.get("duration", 0),
        tags=tags,
        error=has_error,
        links=_span_links(raw_span, trace_id),
        start_time_us=raw_span.get("startTime"),
    )


def parse_spans(raw_traces: list[dict]) -> list[Span]:
    """Convert raw Jaeger trace dicts into typed :class:`Span` objects.

    For each trace, ``processID`` is resolved against the trace-level
    ``processes`` map to obtain the real ``serviceName``. Parent-child
    relationships are resolved from the ``references`` list (``CHILD_OF``
    entries only).

    Args:
        raw_traces: The list of trace dicts returned by
            :func:`load_traces_from_file`.

    Returns:
        A flat list of :class:`Span` objects across all traces.
    """
    return [
        _parse_span(raw_span, trace.get("traceID", ""), trace.get("processes", {}))
        for trace in raw_traces
        for raw_span in trace.get("spans", [])
    ]
