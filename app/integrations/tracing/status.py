"""Is Jaeger up, and is real telemetry arriving?

Everything here is read from Jaeger's query API: which services have reported, and when the newest trace of
each began. Nothing is sent to Jaeger to test it, so a service or trace in the answer was put there by an
application, never by this module.

Telemetry states:
  UNREACHABLE  the query service did not answer; nothing is known about telemetry
  NO_SERVICES  it answers, and no application service has reported yet
  RECEIVING    a trace began within ``fresh_seconds``
  STALE        services have reported, but none within ``fresh_seconds``
"""

from __future__ import annotations

import os
import time
from enum import Enum
from typing import Any, Protocol

DEFAULT_EXCLUDED = "jaeger-all-in-one,jaeger-query,jaeger"
DEFAULT_FRESH_SECONDS = 300
DEFAULT_MAX_SERVICES = 12
LOOKBACK = "1h"


class TelemetryState(str, Enum):
    UNREACHABLE = "UNREACHABLE"
    NO_SERVICES = "NO_SERVICES"
    RECEIVING = "RECEIVING"
    STALE = "STALE"


class TraceSource(Protocol):
    base_url: str

    def is_healthy(self) -> bool: ...
    def get_services(self) -> list[str]: ...
    def fetch_traces(self, service_name: str, limit: int = ..., lookback: str = ...) -> list[dict[str, Any]]: ...


def excluded_services(environ: dict[str, str] | None = None) -> frozenset[str]:
    """Jaeger's own self-tracing services: they are not applications and must not look like telemetry."""
    env = os.environ if environ is None else environ
    raw = env.get("TRACING_EXCLUDED_SERVICES")
    return frozenset(name.strip().lower() for name in (raw if raw is not None else DEFAULT_EXCLUDED).split(",") if name.strip())


def _iso(epoch_seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


def newest_trace_start(traces: list[dict[str, Any]]) -> float | None:
    """When the newest of ``traces`` began, in epoch seconds, or None if no span carries a start time."""
    newest: float | None = None
    for trace in traces:
        starts = [
            span["startTime"]
            for span in (trace.get("spans") or [])
            if isinstance(span, dict) and isinstance(span.get("startTime"), (int, float)) and not isinstance(span.get("startTime"), bool)
        ]
        if starts:
            began = min(starts) / 1_000_000  # Jaeger reports microseconds
            newest = began if newest is None else max(newest, began)
    return newest


def last_trace_at(source: TraceSource, service: str) -> float | None:
    """The newest trace start of one service within the lookback, or None when it has none there."""
    return newest_trace_start(source.fetch_traces(service, limit=1, lookback=LOOKBACK))


def read_status(
    source: TraceSource,
    *,
    now: float | None = None,
    fresh_seconds: int = DEFAULT_FRESH_SECONDS,
    max_services: int = DEFAULT_MAX_SERVICES,
    excluded: frozenset[str] | None = None,
) -> dict[str, Any]:
    """The query service's reachability and what telemetry has arrived, as a plain mapping."""
    observed = time.time() if now is None else now
    skip = excluded_services() if excluded is None else excluded
    answer: dict[str, Any] = {
        "query": {"url": source.base_url, "reachable": False},
        "telemetry": {
            "state": TelemetryState.UNREACHABLE.value,
            "serviceCount": None,
            "services": [],
            "newestTraceAt": None,
            "freshSeconds": fresh_seconds,
            "truncated": False,
            "observedAt": _iso(observed),
        },
    }
    if not source.is_healthy():
        return answer
    answer["query"]["reachable"] = True
    telemetry = answer["telemetry"]

    try:
        names = sorted({name for name in source.get_services() if name.lower() not in skip})
    except ConnectionError:
        # It answered a moment ago and then did not: treat it as unreachable instead of reporting no services.
        answer["query"]["reachable"] = False
        return answer
    telemetry["serviceCount"] = len(names)
    if not names:
        telemetry["state"] = TelemetryState.NO_SERVICES.value
        return answer

    shown = names[:max_services]
    telemetry["truncated"] = len(names) > len(shown)
    newest: float | None = None
    for name in shown:
        try:
            began = last_trace_at(source, name)
        except ConnectionError:
            began = None
        telemetry["services"].append({"name": name, "lastTraceAt": _iso(began) if began is not None else None})
        if began is not None:
            newest = began if newest is None else max(newest, began)

    telemetry["newestTraceAt"] = _iso(newest) if newest is not None else None
    fresh = newest is not None and observed - newest <= fresh_seconds
    telemetry["state"] = (TelemetryState.RECEIVING if fresh else TelemetryState.STALE).value
    return answer
