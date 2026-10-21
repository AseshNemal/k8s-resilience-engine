"""Cross-backend normalization and evidence-strength tests."""

import httpx
import pytest

from app.api.workflows import inspect_telemetry, normalize_telemetry

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.engine.workflow_dependency_modeling.telemetry_normalizer import (
    normalize_non_causal_edges,
    inspect_telemetry_payload,
    normalize_otlp_traces,
    normalize_telemetry_payload,
    normalize_zipkin_traces,
)
from app.engine.workflow_dependency_modeling.trace_reader import parse_spans
from app.engine.workflow_dependency_modeling.trace_validator import (
    TraceQuality,
    validate_trace_quality,
)


TRACE_ID = "0123456789abcdef0123456789abcdef"


def _zipkin_trace():
    return [
        {
            "traceId": TRACE_ID,
            "id": "1111111111111111",
            "name": "POST /checkout",
            "kind": "SERVER",
            "timestamp": 1_700_000_000_000_000,
            "duration": 1_000,
            "localEndpoint": {"serviceName": "frontend"},
            "tags": {"http.status_code": "200"},
        },
        {
            "traceId": TRACE_ID,
            "id": "2222222222222222",
            "parentId": "1111111111111111",
            "name": "Charge",
            "kind": "SERVER",
            "timestamp": 1_700_000_000_000_100,
            "duration": 500,
            "localEndpoint": {"serviceName": "paymentservice"},
        },
    ]


def _otlp_payload():
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "frontend"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": TRACE_ID,
                                "spanId": "1111111111111111",
                                "name": "POST /checkout",
                                "kind": 2,
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000000001000000",
                                "status": {"code": 1},
                            }
                        ]
                    }
                ],
            },
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "checkoutservice"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": TRACE_ID,
                                "spanId": "2222222222222222",
                                "parentSpanId": "1111111111111111",
                                "name": "PlaceOrder",
                                "kind": "SPAN_KIND_SERVER",
                                "startTimeUnixNano": "1700000000000100000",
                                "endTimeUnixNano": "1700000000000600000",
                                "status": {"code": "STATUS_CODE_OK"},
                            }
                        ]
                    }
                ],
            },
        ]
    }


def test_zipkin_v2_normalizes_to_valid_parent_linked_trace() -> None:
    traces = normalize_zipkin_traces([_zipkin_trace()])
    spans = parse_spans(traces)

    assert len(traces) == 1
    assert [span.service_name for span in spans] == ["frontend", "paymentservice"]
    assert spans[1].parent_span_id == spans[0].span_id
    assert validate_trace_quality(traces).results[0].quality is TraceQuality.VALID


def test_tempo_otlp_json_normalizes_to_valid_parent_linked_trace() -> None:
    traces = normalize_otlp_traces(_otlp_payload())
    spans = parse_spans(traces)

    assert len(traces) == 1
    assert spans[0].duration_us == 1_000
    assert spans[1].service_name == "checkoutservice"
    assert spans[1].tags["otel.status_code"] == "OK"
    assert validate_trace_quality(traces).results[0].quality is TraceQuality.VALID


def test_otlp_span_links_survive_normalization_as_non_parent_references() -> None:
    payload = _otlp_payload()
    linked = payload["resourceSpans"][1]["scopeSpans"][0]["spans"][0]
    linked["links"] = [
        {"traceId": TRACE_ID, "spanId": "1111111111111111"}
    ]
    spans = parse_spans(normalize_otlp_traces(payload))
    assert len(spans[1].links) == 1
    assert spans[1].links[0].trace_id == TRACE_ID
    assert spans[1].links[0].span_id == "1111111111111111"
    assert spans[1].links[0].reference_type == "SPAN_LINK"

    inspected = inspect_telemetry_payload(
        {"sourceType": "tempo", "payload": payload}
    )
    assert inspected["linkedEdges"] == [
        {
            "source": "frontend",
            "target": "checkoutservice",
            "evidenceType": "SPAN_LINK",
            "occurrenceCount": 1,
            "frequency": 1.0,
        }
    ]


def test_logs_and_network_remain_explicitly_non_causal() -> None:
    result = normalize_telemetry_payload(
        {
            "sourceType": "correlated_logs",
            "payload": {
                "entries": [
                    {
                        "sourceService": "frontend",
                        "targetService": "cartservice",
                        "timestamp": "2026-10-13T10:00:00Z",
                        "correlationId": "request-1",
                    },
                    {
                        "sourceService": "frontend",
                        "targetService": "cartservice",
                        "timestamp": "2026-10-13T10:00:01Z",
                        "correlationId": "request-2",
                    },
                ]
            },
        }
    )

    assert result["causal"] is False
    assert result["evidenceLabel"] == "LOG_CORRELATED"
    assert result["edges"][0]["observationCount"] == 2
    assert result["edges"][0]["causal"] is False
    assert "traces" not in result


def test_correlated_log_without_correlation_id_is_rejected() -> None:
    with pytest.raises(ComponentDataError, match="correlationId"):
        normalize_non_causal_edges(
            [
                {
                    "source": "frontend",
                    "target": "cartservice",
                    "timestamp": "2026-10-13T10:00:00Z",
                }
            ],
            source_type="correlated_logs",
        )


def test_normalization_api_handler_exposes_causal_contract() -> None:
    response = normalize_telemetry(
        {"sourceType": "zipkin", "payload": [_zipkin_trace()]}
    )

    assert response["traceCount"] == 1
    assert response["causal"] is True


def test_offline_jaeger_inspection_returns_quality_workflows_and_edges() -> None:
    traces = normalize_zipkin_traces([_zipkin_trace()])

    result = inspect_telemetry_payload(
        {"sourceType": "offline_jaeger", "payload": {"data": traces}}
    )

    assert result["causal"] is True
    assert result["eligibleTraceCount"] == 1
    assert result["observedServices"] == ["frontend", "paymentservice"]
    assert result["edges"] == [
        {
            "source": "frontend",
            "target": "paymentservice",
            "occurrenceCount": 1,
            "frequency": 1.0,
            "totalCallCount": 1,
            "maxCallsPerTrace": 1,
            "repeatedTraceCount": 0,
        }
    ]
    assert result["linkedEdges"] == []
    assert result["discoveredWorkflows"][0]["operationName"] == "POST /checkout"
    assert "traces" not in result


def test_inspection_api_handler_preserves_non_causal_label() -> None:
    result = inspect_telemetry(
        {
            "sourceType": "network_observations",
            "payload": [
                {
                    "source": "frontend",
                    "target": "cartservice",
                    "timestamp": "2026-10-13T10:00:00Z",
                }
            ],
        }
    )

    assert result["causal"] is False
    assert result["evidenceLabel"] == "NETWORK_OBSERVED"

