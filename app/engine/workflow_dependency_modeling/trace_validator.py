"""Deterministic trace-quality checks used before dependency reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from enum import Enum
from ipaddress import ip_address
from typing import Any, Mapping, Sequence


class TraceQuality(str, Enum):
    VALID = "VALID"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"


@dataclass(frozen=True)
class TraceQualityResult:
    trace_id: str
    quality: TraceQuality
    span_count: int
    root_span_count: int
    disconnected_root_count: int
    broken_parent_count: int
    unknown_service_count: int
    missing_service_identities: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        """Only complete traces enter primary classification denominators."""
        return self.quality is TraceQuality.VALID

    def to_mapping(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "quality": self.quality.value,
            "eligible": self.eligible,
            "spanCount": self.span_count,
            "rootSpanCount": self.root_span_count,
            "disconnectedRootCount": self.disconnected_root_count,
            "brokenParentCount": self.broken_parent_count,
            "unknownServiceCount": self.unknown_service_count,
            "missingServiceIdentities": list(self.missing_service_identities),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class TraceQualitySummary:
    results: tuple[TraceQualityResult, ...]

    @property
    def valid_count(self) -> int:
        return sum(item.quality is TraceQuality.VALID for item in self.results)

    @property
    def partial_count(self) -> int:
        return sum(item.quality is TraceQuality.PARTIAL for item in self.results)

    @property
    def invalid_count(self) -> int:
        return sum(item.quality is TraceQuality.INVALID for item in self.results)

    @property
    def excluded_count(self) -> int:
        return self.partial_count + self.invalid_count

    @property
    def eligible_trace_ids(self) -> tuple[str, ...]:
        return tuple(item.trace_id for item in self.results if item.eligible)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "totalCount": len(self.results),
            "validCount": self.valid_count,
            "partialCount": self.partial_count,
            "invalidCount": self.invalid_count,
            "excludedCount": self.excluded_count,
            "results": [item.to_mapping() for item in self.results],
        }


def _parent_id(span: Mapping[str, Any]) -> str | None:
    references = span.get("references", [])
    for reference in references if isinstance(references, list) else []:
        if isinstance(reference, Mapping) and reference.get("refType") == "CHILD_OF":
            value = reference.get("spanID")
            return str(value).strip() if value else None
    return None


def _trace_parts(
    trace: Mapping[str, Any], index: int
) -> tuple[str, bool, list[Mapping[str, Any]], Mapping[str, Any]]:
    raw_trace_id = str(trace.get("traceID", "")).strip()
    raw_spans = trace.get("spans")
    spans = raw_spans if isinstance(raw_spans, list) else []
    raw_processes = trace.get("processes")
    processes = raw_processes if isinstance(raw_processes, Mapping) else {}
    return raw_trace_id or f"missing-trace-id:{index}", bool(raw_trace_id), spans, processes


def _span_metrics(
    spans: list[Mapping[str, Any]], processes: Mapping[str, Any]
) -> tuple[int, bool, int, int, int, int, tuple[str, ...]]:
    span_ids = [str(span.get("spanID", "")).strip() for span in spans]
    usable_span_ids = {span_id for span_id in span_ids if span_id}
    populated_span_ids = [span_id for span_id in span_ids if span_id]
    duplicate_ids = len(usable_span_ids) != len(populated_span_ids)
    missing_span_ids = len(spans) - len(populated_span_ids)
    root_count = sum(
        _parent_id(span) is None or _parent_id(span) not in usable_span_ids
        for span in spans
    )
    # Span links are associations, not parent-child connectivity evidence.
    disconnected_root_count = root_count
    # A single missing parent is the normal W3C remote-parent boundary for an
    # externally initiated trace. Additional local roots expose partial data.
    broken_parent_count = max(disconnected_root_count - 1, 0)
    unknown_service_count = sum(
        1
        for span in spans
        if not str(
            processes.get(str(span.get("processID", "")), {}).get("serviceName", "")
        ).strip()
    )
    observed_services = {
        str(process.get("serviceName", "")).strip().lower()
        for process in processes.values()
        if isinstance(process, Mapping)
    }
    span_by_id = {str(span.get("spanID", "")): span for span in spans}
    # An address is not a service name. For IP peers, use an actual linked
    # server span as identity evidence; otherwise retain the unresolved address.
    resolved_client_ids = {
        _parent_id(span)
        for span in spans
        if any(
            isinstance(tag, Mapping)
            and tag.get("key") == "span.kind"
            and str(tag.get("value", "")).lower() == "server"
            for tag in (span.get("tags") if isinstance(span.get("tags"), list) else [])
        )
        and str(processes.get(str(span.get("processID", "")), {}).get("serviceName", "")).strip()
        and processes.get(str(span.get("processID", "")), {}).get("serviceName")
        != processes.get(str(span_by_id.get(_parent_id(span), {}).get("processID", "")), {}).get("serviceName")
    }
    missing_service_identities: set[str] = set()
    for span in spans:
        tags = {
            str(tag.get("key", "")): tag.get("value")
            for tag in (span.get("tags") if isinstance(span.get("tags"), list) else [])
            if isinstance(tag, Mapping)
        }
        if str(tags.get("span.kind", "")).lower() != "client":
            continue
        raw_target = tags.get("server.address") or tags.get("net.peer.name")
        if not isinstance(raw_target, str):
            continue
        if str(span.get("spanID", "")).strip() in resolved_client_ids:
            continue
        address = raw_target.strip().lower()
        if address.startswith("[") and "]" in address:
            address = address[1:address.index("]")]
        elif address.count(":") == 1:
            address = address.split(":", 1)[0]
        try:
            ip_address(address)
        except ValueError:
            pass
        else:
            if str(span.get("spanID", "")).strip() not in resolved_client_ids:
                missing_service_identities.add(address)
            continue
        target = raw_target.strip().lower().split(".", 1)[0].split(":", 1)[0]
        if target and target not in observed_services and target != "localhost":
            missing_service_identities.add(target)
    return (
        missing_span_ids,
        duplicate_ids,
        root_count,
        disconnected_root_count,
        broken_parent_count,
        unknown_service_count,
        tuple(sorted(missing_service_identities)),
    )


def _quality_reasons(
    has_trace_id: bool,
    spans: list[Mapping[str, Any]],
    missing_span_ids: int,
    duplicate_ids: bool,
    root_count: int,
    disconnected_root_count: int,
    broken_parent_count: int,
    unknown_service_count: int,
    missing_service_identities: tuple[str, ...],
) -> list[str]:
    reasons: list[str] = []
    if not has_trace_id:
        reasons.append("Trace ID is missing")
    if not spans:
        reasons.append("Trace contains no spans")
    if missing_span_ids:
        reasons.append(f"{missing_span_ids} span(s) have no span ID")
    if duplicate_ids:
        reasons.append("Trace contains duplicate span IDs")
    if spans and root_count == 0:
        reasons.append("Trace has no root span")
    if broken_parent_count:
        reasons.append(f"{broken_parent_count} span(s) reference a missing parent")
    if unknown_service_count:
        reasons.append(
            f"{unknown_service_count} span(s) have unresolved service identity"
        )
    if missing_service_identities:
        reasons.append(
            "Client spans have no server-side service identity for: "
            + ", ".join(missing_service_identities)
        )
    if disconnected_root_count > 1:
        reasons.append(
            f"Trace contains {disconnected_root_count} disconnected root spans"
        )
    return reasons


def _trace_quality(
    structurally_invalid: bool,
    broken_parent_count: int,
    unknown_service_count: int,
    disconnected_root_count: int,
    missing_service_identities: tuple[str, ...],
) -> TraceQuality:
    if structurally_invalid:
        return TraceQuality.INVALID
    if (
        broken_parent_count
        or unknown_service_count
        or disconnected_root_count != 1
        or missing_service_identities
    ):
        return TraceQuality.PARTIAL
    return TraceQuality.VALID


def validate_trace_quality(raw_traces: Sequence[Mapping[str, Any]]) -> TraceQualitySummary:
    """Classify raw Jaeger traces without inventing missing relationships."""
    if not isinstance(raw_traces, (list, tuple)) or any(
        not isinstance(trace, Mapping) for trace in raw_traces
    ):
        raise ValueError("traces must be a list of trace objects")
    results: list[TraceQualityResult] = []
    trace_counts = Counter(str(trace.get("traceID", "")).strip() for trace in raw_traces)
    for index, trace in enumerate(raw_traces):
        trace_id, has_trace_id, spans, process_map = _trace_parts(trace, index)
        malformed = any(not isinstance(span, Mapping) for span in spans)
        spans = [span for span in spans if isinstance(span, Mapping)]
        structural_reasons: list[str] = []
        if not isinstance(trace.get("traceID"), str) or not trace["traceID"].strip():
            structural_reasons.append("Trace ID must be a non-empty string")
        # Sanitize only the local inspection copy. Malformed nested values must
        # become reported exclusions, never parser exceptions or invented IDs.
        checked_spans = []
        for span in spans:
            checked = dict(span)
            for key in ("spanID", "processID"):
                if not isinstance(span.get(key), str) or not span[key].strip():
                    structural_reasons.append(f"Span {key} must be a non-empty string")
            duration = span.get("duration", 0)
            if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
                structural_reasons.append("Span duration must be a non-negative integer")
            if "operationName" in span and not isinstance(span["operationName"], str):
                structural_reasons.append("Span operationName must be a string")
            for key in ("references", "tags"):
                values = span.get(key, [])
                if not isinstance(values, list):
                    structural_reasons.append(f"Span {key} must be a list")
                    values = []
                if any(not isinstance(value, Mapping) for value in values):
                    structural_reasons.append(f"Span {key} must contain objects")
                checked[key] = [value for value in values if isinstance(value, Mapping)]
            for reference in checked["references"]:
                if not isinstance(reference.get("spanID"), str) or not reference["spanID"].strip():
                    structural_reasons.append("Reference spanID must be a non-empty string")
            for tag in checked["tags"]:
                if not isinstance(tag.get("key"), str) or not tag["key"].strip():
                    structural_reasons.append("Tag key must be a non-empty string")
            checked_spans.append(checked)
        spans = checked_spans
        if any(not isinstance(process, Mapping) for process in process_map.values()):
            structural_reasons.append("Trace contains a non-object process")
            process_map = {key: value for key, value in process_map.items() if isinstance(value, Mapping)}
        if any(
            "serviceName" in process_map.get(str(span.get("processID", "")), {})
            and not isinstance(process_map[str(span.get("processID", ""))]["serviceName"], str)
            for span in spans
        ):
            structural_reasons.append("Process serviceName must be a string")
        if malformed:
            structural_reasons.append("Trace contains a non-object span")
        if trace_counts[trace_id] > 1:
            structural_reasons.append("Input contains duplicate trace IDs")
        parents = {str(span.get("spanID", "")): _parent_id(span) for span in spans}
        visited: set[str] = set()
        for span_id in parents:
            path: set[str] = set()
            current = span_id
            while current in parents and current not in visited:
                if current in path:
                    structural_reasons.append("Trace contains a parent cycle")
                    break
                path.add(current)
                current = parents[current]
            visited.update(path)
        for span in spans:
            if span.get("traceID", trace_id) != trace_id:
                structural_reasons.append("Span belongs to a different trace")
            references = span.get("references", [])
            if not isinstance(references, list):
                structural_reasons.append("Span references must be a list")
                references = []
            if not isinstance(span.get("tags", []), list):
                structural_reasons.append("Span tags must be a list")
            parent_refs = [ref for ref in references if isinstance(ref, Mapping) and ref.get("refType") == "CHILD_OF"]
            if len(parent_refs) > 1:
                structural_reasons.append("Span has multiple parents")
            if any(ref.get("traceID", trace_id) != trace_id for ref in parent_refs):
                structural_reasons.append("Parent reference belongs to a different trace")
        metrics = _span_metrics(spans, process_map)
        (
            missing_span_ids,
            duplicate_ids,
            root_count,
            disconnected_roots,
            broken_parents,
            unknown_services,
            missing_service_identities,
        ) = metrics
        structurally_invalid = bool(
            not has_trace_id
            or not spans
            or missing_span_ids
            or duplicate_ids
            or (spans and disconnected_roots == 0)
            or structural_reasons
        )
        results.append(
            TraceQualityResult(
                trace_id=trace_id,
                quality=_trace_quality(
                    structurally_invalid,
                    broken_parents,
                    unknown_services,
                    disconnected_roots,
                    missing_service_identities,
                ),
                span_count=len(spans),
                root_span_count=root_count,
                disconnected_root_count=disconnected_roots,
                broken_parent_count=broken_parents,
                unknown_service_count=unknown_services,
                missing_service_identities=missing_service_identities,
                reasons=tuple(
                    structural_reasons + _quality_reasons(
                        has_trace_id,
                        spans,
                        missing_span_ids,
                        duplicate_ids,
                        root_count,
                        disconnected_roots,
                        broken_parents,
                        unknown_services,
                        missing_service_identities,
                    )
                ),
            )
        )

    return TraceQualitySummary(tuple(results))


def eligible_traces(
    raw_traces: Sequence[Mapping[str, Any]], summary: TraceQualitySummary
) -> list[dict[str, Any]]:
    """Return valid raw traces in input order for primary analysis."""
    if len(raw_traces) != len(summary.results):
        raise ValueError("Trace quality summary must align with input traces")
    return [dict(trace) for trace, result in zip(raw_traces, summary.results) if result.eligible and trace.get("traceID") == result.trace_id]
