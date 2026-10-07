"""Trace-quality gating and broken-parent provenance tests."""

from app.engine.workflow_dependency_modeling.interaction_extractor import extract_interactions
from app.engine.workflow_dependency_modeling.trace_reader import parse_spans
from app.engine.workflow_dependency_modeling.trace_validator import (
    TraceQuality,
    eligible_traces,
    validate_trace_quality,
)


def _trace(trace_id: str, spans: list[dict], processes: dict | None = None) -> dict:
    return {
        "traceID": trace_id,
        "spans": spans,
        "processes": processes or {"p1": {"serviceName": "frontend"}},
    }


def _span(span_id: str, parent_id: str | None = None, process_id: str = "p1") -> dict:
    references = [] if parent_id is None else [
        {"refType": "CHILD_OF", "spanID": parent_id}
    ]
    return {
        "spanID": span_id,
        "references": references,
        "processID": process_id,
        "operationName": "operation",
    }


def test_quality_summary_records_valid_partial_and_invalid_exclusions() -> None:
    traces = [
        _trace("valid", [_span("root")]),
        _trace("partial", [_span("root"), _span("child", "missing")]),
        _trace("invalid", []),
    ]

    summary = validate_trace_quality(traces)

    assert [item.quality for item in summary.results] == [
        TraceQuality.VALID,
        TraceQuality.PARTIAL,
        TraceQuality.INVALID,
    ]
    assert summary.valid_count == 1
    assert summary.partial_count == 1
    assert summary.invalid_count == 1
    assert summary.excluded_count == 2
    assert [trace["traceID"] for trace in eligible_traces(traces, summary)] == ["valid"]


def test_extractor_records_broken_parent_instead_of_dropping_silently() -> None:
    raw = _trace("partial", [_span("root"), _span("child", "missing")])

    interaction = extract_interactions(parse_spans([raw]))[0]

    assert interaction.broken_parent_count == 1


def test_root_only_service_remains_observable() -> None:
    interaction = extract_interactions(parse_spans([_trace("root-only", [_span("root")])]))[0]

    assert interaction.observed_services == ["frontend"]
    assert interaction.edges == []
