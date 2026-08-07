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
    spans: list[Span] = []

    for trace in raw_traces:
        trace_id: str = trace.get("traceID", "")
        processes: dict[str, dict] = trace.get("processes", {})

        for raw_span in trace.get("spans", []):
            # Resolve the service name through the process map ---------------
            process_id: str = raw_span.get("processID", "")
            process: dict = processes.get(process_id, {})
            service_name: str = process.get("serviceName", "unknown")

            # Resolve the parent span ID from CHILD_OF references -----------
            parent_span_id: str | None = None
            for ref in raw_span.get("references", []):
                if ref.get("refType") == "CHILD_OF":
                    ref_span_id = ref.get("spanID", "")
                    if ref_span_id:
                        parent_span_id = ref_span_id
                    break

            # Flatten tags to dict[str, str]; detect error flag -------------
            tags: dict[str, str] = {}
            error: bool = False
            for tag in raw_span.get("tags", []):
                key: str = tag.get("key", "")
                value = tag.get("value")
                tags[key] = str(value)
                if key == "error" and value is True:
                    error = True

            spans.append(
                Span(
                    trace_id=raw_span.get("traceID", trace_id),
                    span_id=raw_span.get("spanID", ""),
                    parent_span_id=parent_span_id,
                    service_name=service_name,
                    operation_name=raw_span.get("operationName", ""),
                    duration_us=raw_span.get("duration", 0),
                    tags=tags,
                    error=error,
                )
            )

    return spans
