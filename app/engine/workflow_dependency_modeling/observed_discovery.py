"""Bounded discovery of observed workflow candidates from a connected trace backend.

No entry service has to be named: the backend's recorded services are scanned (a bounded number, each with a
bounded trace query), traces are deduplicated by trace ID, and each trace is grouped under its own root service
and root operation by the existing root-span discovery. The result is evidence of what was observed. It carries
no business meaning, criticality, dependency class or approval; those need the controlled experiment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping

from app.engine.workflow_dependency_modeling.auto_discovery import (
    discover_root_workflows,
    find_root_spans,
    select_workflow_traces,
)
from app.engine.workflow_dependency_modeling.trace_reader import Span, parse_spans
from app.engine.workflow_dependency_modeling.trace_source import ServiceListingTraceSource
from app.engine.workflow_dependency_modeling.trace_validator import validate_trace_quality


# Discovery states, in the order a page should rule them out.
JAEGER_UNAVAILABLE = "jaeger_unavailable"
NO_TRACES = "no_traces"
NO_CANDIDATES = "no_candidates"
INCOMPLETE_TRACES = "incomplete_traces"
DISCOVERED = "discovered"

# Hard ceilings, so that no setting can turn discovery into an unbounded read of trace history.
_CEILINGS = {"max_services": 50, "traces_per_service": 1000, "max_traces": 5000}
# The backend's own tracing is not application activity.
_DEFAULT_EXCLUDED_SERVICES = "jaeger-all-in-one,jaeger-query,jaeger"
_LOOKBACK = re.compile(r"^([1-9]\d*)([smhdw])$")
_UNIT_MS = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


@dataclass(frozen=True)
class DiscoveryLimits:
    """How much of the trace backend one discovery may read."""

    max_services: int = 12
    traces_per_service: int = 50
    max_traces: int = 500

    def __post_init__(self) -> None:
        for name, ceiling in _CEILINGS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be an integer between 1 and {ceiling}")

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None, max_traces: int | None = None
    ) -> "DiscoveryLimits":
        """Defaults, overridden by the backend's own settings, then by a caller's total-trace limit."""
        env = os.environ if environ is None else environ
        values: dict[str, int] = {}
        for name, variable in (
            ("max_services", "WORKFLOW_DISCOVERY_MAX_SERVICES"),
            ("traces_per_service", "WORKFLOW_DISCOVERY_TRACES_PER_SERVICE"),
            ("max_traces", "WORKFLOW_DISCOVERY_MAX_TRACES"),
        ):
            raw = (env.get(variable) or "").strip()
            if not raw:
                continue
            try:
                values[name] = int(raw)
            except ValueError as err:
                raise ValueError(f"{variable} must be an integer") from err
        if max_traces is not None:
            values["max_traces"] = max_traces
        limits = cls(**values)
        # A service query can never usefully return more than the total.
        return cls(
            max_services=limits.max_services,
            traces_per_service=min(limits.traces_per_service, limits.max_traces),
            max_traces=limits.max_traces,
        )

    def to_mapping(self) -> dict[str, int]:
        return {
            "maxServices": self.max_services,
            "tracesPerService": self.traces_per_service,
            "maxTraces": self.max_traces,
        }


def lookback_to_ms(lookback: str) -> int:
    match = _LOOKBACK.fullmatch(lookback)
    if match is None:
        raise ValueError("lookback must use a positive duration such as 30m, 1h, or 2d")
    return int(match.group(1)) * _UNIT_MS[match.group(2)]


def read_trace_window(
    source: Any,
    service_name: str,
    lookback: str,
    *,
    page_size: int,
    max_queries: int = 64,
    now_us: int | None = None,
) -> list[dict[str, Any]]:
    """Every trace of ``service_name`` in the last ``lookback``, not just the first page.

    A full page means the window may hold more, so it is split in two (boundaries overlap; trace IDs
    deduplicate) until every page comes back below the limit. Raises ``ValueError`` when that would take
    more than ``max_queries`` queries, or when a window cannot be split any further, rather than returning
    a partial read as if it were complete.
    """
    query = source.fetch_trace_window
    end = now_us if now_us is not None else time.time_ns() // 1000
    start = end - lookback_to_ms(lookback) * 1000
    resolution = getattr(source, "discovery_resolution_us", 1)
    pending = [(start, end)]
    found: dict[str, dict[str, Any]] = {}
    queries = 0
    while pending:
        if queries >= max_queries:
            raise ValueError(f"The {lookback} window holds more traces than {max_queries} queries of {page_size} can read; choose a shorter time window")
        window_start, window_end = pending.pop()
        queries += 1
        batch = query(service_name, window_start, window_end, page_size)
        for trace in batch:
            trace_id = str(trace.get("traceID") or "")
            if trace_id:
                found.setdefault(trace_id, trace)
        if len(batch) >= page_size:
            if window_end - window_start <= resolution:
                raise ValueError("More traces share one timestamp than one query can return; the window cannot be read completely")
            middle = ((window_start + window_end) // 2 // resolution) * resolution
            if not window_start < middle < window_end:
                middle = window_start + resolution
            pending.extend([(window_start, middle), (middle, window_end)])
    return list(found.values())


@dataclass(frozen=True)
class TraceCollection:
    """The traces one bounded scan read, and how much of the backend it did not read."""

    traces: list[dict[str, Any]]
    services_recorded: list[str]
    services_scanned: list[str]
    services_skipped: list[str]  # not scanned because a limit was reached
    truncated: bool
    # "call_graph": only services no other service calls were scanned as entry points. "scan_all": the call
    # graph was unavailable (or turned off), so every recorded service was scanned in turn.
    entry_detection: str = "scan_all"
    services_called_by_others: tuple[str, ...] = ()  # not scanned: their traces come from their callers' entries
    services_unexamined: tuple[str, ...] = ()  # skipped, and in none of the traces that were read


def _excluded_services(environ: Mapping[str, str] | None) -> set[str]:
    env = os.environ if environ is None else environ
    raw = env.get("WORKFLOW_DISCOVERY_EXCLUDED_SERVICES")
    raw = _DEFAULT_EXCLUDED_SERVICES if raw is None else raw
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _entry_order(
    source: ServiceListingTraceSource,
    recorded: list[str],
    lookback: str,
    environ: Mapping[str, str] | None,
) -> tuple[list[str], tuple[str, ...], str]:
    """The services to scan, in order: entry points from the call graph if it is known, else all of them.

    A service no other service calls is an entry point, and the traces of its callees are inside its own, so
    scanning only entry points reads each trace about once instead of once per service it touches. The call
    graph can be missing (storage that needs a batch job) or stale, so an empty or failing answer falls back to
    scanning every recorded service, and the services left out are named in the report either way.
    """
    env = os.environ if environ is None else environ
    list_edges = getattr(source, "list_call_edges", None)
    if (env.get("WORKFLOW_DISCOVERY_ENTRY_DETECTION") or "auto").strip().lower() == "scan_all" or list_edges is None:
        return recorded, (), "scan_all"
    try:
        edges = list_edges(lookback_to_ms(lookback))
    except ConnectionError:
        return recorded, (), "scan_all"
    known = set(recorded)
    edges = [(caller, callee) for caller, callee in edges if caller in known and callee in known and caller != callee]
    if not edges:
        return recorded, (), "scan_all"
    callees = {callee for _, callee in edges}
    calls_made = Counter(caller for caller, _ in edges)
    entries = sorted((name for name in recorded if name not in callees), key=lambda n: (-calls_made[n], n))
    return entries, tuple(sorted(callees)), "call_graph"


def collect_traces(
    source: ServiceListingTraceSource,
    *,
    lookback: str,
    limits: DiscoveryLimits,
    environ: Mapping[str, str] | None = None,
) -> TraceCollection:
    """Read recent traces for the entry services, within ``limits`` and deduplicated by trace ID.

    Raises ``ConnectionError`` when the backend cannot be read.
    """
    excluded = _excluded_services(environ)
    recorded = sorted(
        {name for name in source.list_services() if name and name.lower() not in excluded}
    )
    order, called_by_others, detection = _entry_order(source, recorded, lookback, environ)
    by_id: dict[str, dict[str, Any]] = {}
    scanned: list[str] = []
    truncated = False
    for name in order:
        if len(scanned) >= limits.max_services or len(by_id) >= limits.max_traces:
            break
        batch = source.fetch_workflow_traces(
            service_name=name,
            operation="",
            lookback=lookback,
            limit=limits.traces_per_service,
        )
        scanned.append(name)
        # A full page means the service has more traces in the window than were read.
        truncated = truncated or len(batch) >= limits.traces_per_service
        for trace in batch:
            trace_id = str(trace.get("traceID") or "")
            if trace_id and trace_id not in by_id:
                by_id[trace_id] = trace
    traces = list(by_id.values())
    if len(traces) > limits.max_traces:
        traces = traces[: limits.max_traces]
        truncated = True
    skipped = [name for name in order if name not in scanned]
    seen = {
        str(process.get("serviceName", ""))
        for trace in traces
        for process in (trace.get("processes") or {}).values()
        if isinstance(process, Mapping)
    }
    return TraceCollection(
        traces=traces,
        services_recorded=recorded,
        services_scanned=scanned,
        services_skipped=skipped,
        truncated=truncated or bool(skipped),
        entry_detection=detection,
        services_called_by_others=called_by_others,
        services_unexamined=tuple(name for name in skipped if name not in seen),
    )


def _root_service(trace: dict[str, Any]) -> str:
    spans: list[Span] = parse_spans([trace])
    roots = find_root_spans({str(trace.get("traceID", "")): spans})
    return next(iter(roots.values())).service_name if roots else ""


def _candidates(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        service = _root_service(trace)
        if service:
            by_root[service].append(trace)
    candidates = []
    for service, group in by_root.items():
        # No route patterns: nothing about any one application's routes is assumed.
        for summary in discover_root_workflows(group, route_patterns=()):
            quality = validate_trace_quality(select_workflow_traces(group, summary.operation_name))
            if quality.valid_count == summary.trace_count:
                evidence_status = "complete"
            elif quality.valid_count:
                evidence_status = "incomplete"
            else:
                evidence_status = "unusable"
            candidates.append(
                {
                    "workflowId": summary.workflow_id,
                    "operationName": summary.operation_name,
                    "rootService": service,
                    "traceCount": summary.trace_count,
                    "participatingServices": summary.participating_services,
                    "sampleTraceIds": summary.sample_trace_ids,
                    "status": "candidate",
                    "source": "live_unverified",
                    "classificationStatus": "not_classified",
                    "evidenceStatus": evidence_status,
                    "traceQuality": {
                        "validCount": quality.valid_count,
                        "partialCount": quality.partial_count,
                        "invalidCount": quality.invalid_count,
                    },
                }
            )
    candidates.sort(key=lambda c: (-c["traceCount"], c["rootService"], c["operationName"]))
    return candidates


def build_report(
    collection: TraceCollection, *, jaeger_url: str, lookback: str, limits: DiscoveryLimits
) -> dict[str, Any]:
    """The discovery envelope: a state a page can switch on, the candidates, and what was and was not read."""
    candidates = _candidates(collection.traces)
    if not collection.traces:
        state = NO_TRACES
        message = "The trace service is connected, but no traces were recorded in this time window."
    elif not candidates:
        state = NO_CANDIDATES
        message = (
            "Traces were recorded, but none start at a business operation "
            "(only health checks, telemetry or exporter calls)."
        )
    elif all(c["evidenceStatus"] == "unusable" for c in candidates):
        state = INCOMPLETE_TRACES
        message = (
            "Traces were recorded, but their evidence is incomplete: missing parents, several roots or "
            "unidentified services. Repair trace propagation or instrumentation before relying on them."
        )
    else:
        state = DISCOVERED
        count = len(candidates)
        message = f"Found {count} observed workflow candidate{'' if count == 1 else 's'}."
    quality_total = sum(c["traceQuality"]["validCount"] for c in candidates)
    # Identifies what the page would show, so a poller can tell an unchanged answer from a new one.
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                state,
                [
                    [c["rootService"], c["operationName"], c["traceCount"], c["participatingServices"], c["evidenceStatus"]]
                    for c in candidates
                ],
            ]
        ).encode()
    ).hexdigest()[:16]
    return {
        "state": state,
        "message": message,
        "retryable": state != DISCOVERED,
        "jaegerUrl": jaeger_url,
        "lookback": lookback,
        "limits": limits.to_mapping(),
        "servicesRecorded": collection.services_recorded,
        "servicesScanned": collection.services_scanned,
        "servicesSkipped": collection.services_skipped,
        "servicesUnexamined": list(collection.services_unexamined),
        "servicesCalledByOthers": list(collection.services_called_by_others),
        "entryDetection": collection.entry_detection,
        "fingerprint": fingerprint,
        "tracesFetched": len(collection.traces),
        "tracesWithCompleteEvidence": quality_total,
        "truncated": collection.truncated,
        "workflows": candidates,
    }


def unavailable_report(*, jaeger_url: str, lookback: str, limits: DiscoveryLimits) -> dict[str, Any]:
    """The envelope for a backend that could not be read. Raw error text is never included."""
    return {
        "state": JAEGER_UNAVAILABLE,
        "message": "The trace service could not be reached.",
        "retryable": True,
        "jaegerUrl": jaeger_url,
        "lookback": lookback,
        "limits": limits.to_mapping(),
        "servicesRecorded": [],
        "servicesScanned": [],
        "servicesSkipped": [],
        "servicesUnexamined": [],
        "servicesCalledByOthers": [],
        "entryDetection": "scan_all",
        "fingerprint": "",
        "tracesFetched": 0,
        "tracesWithCompleteEvidence": 0,
        "truncated": False,
        "workflows": [],
    }


def discover_observed_workflows(
    source: ServiceListingTraceSource,
    *,
    jaeger_url: str,
    lookback: str,
    limits: DiscoveryLimits,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Scan the backend within ``limits`` and report what was observed."""
    try:
        collection = collect_traces(source, lookback=lookback, limits=limits, environ=environ)
    except ConnectionError:
        return unavailable_report(jaeger_url=jaeger_url, lookback=lookback, limits=limits)
    return build_report(collection, jaeger_url=jaeger_url, lookback=lookback, limits=limits)
