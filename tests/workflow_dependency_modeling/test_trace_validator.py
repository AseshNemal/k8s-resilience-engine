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
        "tags": [],
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


def test_one_external_parent_is_a_valid_distributed_trace_boundary() -> None:
    summary = validate_trace_quality(
        [_trace("external", [_span("entry", "remote-parent")])]
    )

    assert summary.results[0].quality is TraceQuality.VALID
    assert summary.results[0].root_span_count == 1
    assert summary.results[0].broken_parent_count == 0


def test_client_target_without_server_process_is_partial_evidence() -> None:
    client = _span("client")
    client["tags"] = [
        {"key": "span.kind", "value": "client"},
        {"key": "net.peer.name", "value": "paymentservice"},
    ]

    result = validate_trace_quality([_trace("missing-server", [client])]).results[0]

    assert result.quality is TraceQuality.PARTIAL
    assert result.missing_service_identities == ("paymentservice",)
    assert "paymentservice" in result.reasons[-1]


def test_client_target_recovers_observed_edge_without_inventing_server_span() -> None:
    client = _span("client")
    client["tags"] = [
        {"key": "span.kind", "value": "client"},
        {"key": "rpc.system", "value": "grpc"},
        {"key": "server.address", "value": "paymentservice.default.svc.cluster.local"},
    ]

    interaction = extract_interactions(
        parse_spans([_trace("missing-server", [client])])
    )[0]

    assert [(edge.source, edge.target) for edge in interaction.edges] == [
        ("frontend", "paymentservice")
    ]
    assert interaction.observed_services == ["frontend", "paymentservice"]

    client["tags"][2]["value"] = "10.0.0.25"
    ip_interaction = extract_interactions(
        parse_spans([_trace("ip-only-peer", [client])])
    )[0]
    assert ip_interaction.edges == []
