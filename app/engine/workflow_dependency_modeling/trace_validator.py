"""Deterministic trace-quality checks used before dependency reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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
    broken_parent_count: int
    unknown_service_count: int
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
            "brokenParentCount": self.broken_parent_count,
            "unknownServiceCount": self.unknown_service_count,
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
    for reference in span.get("references", []):
        if reference.get("refType") == "CHILD_OF":
            value = reference.get("spanID")
            return str(value).strip() if value else None
    return None


def validate_trace_quality(raw_traces: Sequence[Mapping[str, Any]]) -> TraceQualitySummary:
    """Classify raw Jaeger traces without inventing missing relationships."""
    results: list[TraceQualityResult] = []
    for index, trace in enumerate(raw_traces):
        trace_id = str(trace.get("traceID", "")).strip() or f"missing-trace-id:{index}"
        raw_spans = trace.get("spans")
        spans = raw_spans if isinstance(raw_spans, list) else []
        processes = trace.get("processes")
        process_map = processes if isinstance(processes, Mapping) else {}
        reasons: list[str] = []

        span_ids = [str(span.get("spanID", "")).strip() for span in spans]
        usable_span_ids = {span_id for span_id in span_ids if span_id}
        duplicate_ids = len(usable_span_ids) != len([item for item in span_ids if item])
        missing_span_ids = sum(not span_id for span_id in span_ids)
        root_count = sum(_parent_id(span) is None for span in spans)
        broken_parent_count = sum(
            1
            for span in spans
            if (parent_id := _parent_id(span)) is not None
            and parent_id not in usable_span_ids
        )
        unknown_service_count = sum(
            1
            for span in spans
            if not str(
                process_map.get(span.get("processID"), {}).get("serviceName", "")
            ).strip()
        )

        if not str(trace.get("traceID", "")).strip():
            reasons.append("Trace ID is missing")
        if not spans:
            reasons.append("Trace contains no spans")
        if missing_span_ids:
            reasons.append(f"{missing_span_ids} span(s) have no span ID")
        if duplicate_ids:
            reasons.append("Trace contains duplicate span IDs")
        if spans and root_count == 0:
            reasons.append("Trace has no root span")

        structurally_invalid = bool(
            not str(trace.get("traceID", "")).strip()
            or not spans
            or missing_span_ids
            or duplicate_ids
            or (spans and root_count == 0)
        )
        if broken_parent_count:
            reasons.append(
                f"{broken_parent_count} span(s) reference a missing parent"
            )
        if unknown_service_count:
            reasons.append(
                f"{unknown_service_count} span(s) have unresolved service identity"
            )

        quality = (
            TraceQuality.INVALID
            if structurally_invalid
            else TraceQuality.PARTIAL
            if broken_parent_count or unknown_service_count or root_count != 1
            else TraceQuality.VALID
        )
        if root_count > 1:
            reasons.append(f"Trace contains {root_count} root spans")
        results.append(
            TraceQualityResult(
                trace_id=trace_id,
                quality=quality,
                span_count=len(spans),
                root_span_count=root_count,
                broken_parent_count=broken_parent_count,
                unknown_service_count=unknown_service_count,
                reasons=tuple(reasons),
            )
        )

    return TraceQualitySummary(tuple(results))


def eligible_traces(
    raw_traces: Sequence[Mapping[str, Any]], summary: TraceQualitySummary
) -> list[dict[str, Any]]:
    """Return valid raw traces in input order for primary analysis."""
    eligible_ids = set(summary.eligible_trace_ids)
    return [dict(trace) for trace in raw_traces if trace.get("traceID") in eligible_ids]
