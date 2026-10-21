"""Tests for the replaceable trace evidence source boundary."""

from typing import Any

import pytest
import httpx

from app.engine.workflow_dependency_modeling.trace_source import (
    JaegerTraceSource,
    OfflineJaegerTraceSource,
    TempoTraceSource,
    TraceSourceConfiguration,
    ZipkinTraceSource,
    create_trace_source,
)
from app.engine.workflow_dependency_modeling.trace_reader import load_traces_from_file


class RecordingJaegerClient:
    def __init__(self) -> None:
        self.query: dict[str, Any] | None = None

    def fetch_traces(self, **query):
        self.query = query
        return [{"traceID": "abc", "spans": [], "processes": {}}]


def test_jaeger_adapter_satisfies_generic_trace_source_contract() -> None:
    client = RecordingJaegerClient()
    source = JaegerTraceSource(client=client)

    traces = source.fetch_workflow_traces(
        service_name="order-api",
        operation="POST /orders",
        lookback="30m",
        limit=250,
    )

    assert traces[0]["traceID"] == "abc"
    assert client.query == {
        "service_name": "order-api",
        "operation": "POST /orders",
        "lookback": "30m",
        "limit": 250,
    }


def test_bearer_authentication_resolves_only_from_secret_environment() -> None:
    configuration = TraceSourceConfiguration.from_mapping(
        {
            "type": "jaeger",
            "endpoint": "https://traces.example.test",
            "authentication": {
                "type": "bearer_env",
                "secretEnv": "JAEGER_API_TOKEN",
            },
        }
    )

    source = JaegerTraceSource.from_configuration(
        configuration,
        secrets={"JAEGER_API_TOKEN": "private-token"},
    )

    assert source.client.headers == {"Authorization": "Bearer private-token"}
    mapping = configuration.to_mapping()
    assert mapping["authentication"] == {
        "type": "bearer_env",
        "secretEnv": "JAEGER_API_TOKEN",
    }
    assert "private-token" not in str(mapping)


def test_missing_bearer_secret_fails_before_querying_trace_backend() -> None:
    configuration = TraceSourceConfiguration.from_mapping(
        {
            "type": "jaeger",
            "endpoint": "https://traces.example.test",
            "authentication": {
                "type": "bearer_env",
                "secretEnv": "JAEGER_API_TOKEN",
            },
        }
    )
    with pytest.raises(ValueError, match="JAEGER_API_TOKEN"):
        JaegerTraceSource.from_configuration(configuration, secrets={})


def test_generic_factory_builds_zipkin_with_secret_resolved_at_runtime() -> None:
    configuration = TraceSourceConfiguration.from_mapping(
        {
            "type": "zipkin",
            "endpoint": "https://zipkin.example.test",
            "authentication": {"type": "bearer_env", "secretEnv": "ZIPKIN_TOKEN"},
        }
    )

    source = create_trace_source(
        configuration, secrets={"ZIPKIN_TOKEN": "runtime-private-token"}
    )

    assert isinstance(source, ZipkinTraceSource)
    assert source.headers == {"Authorization": "Bearer runtime-private-token"}
    assert "runtime-private-token" not in str(configuration.to_mapping())


def test_offline_export_satisfies_the_same_trace_source_contract() -> None:
    traces = load_traces_from_file("sample_data/traces/checkout_traces.json")
    source = OfflineJaegerTraceSource.from_payload({"data": traces})

    fetched = source.fetch_workflow_traces(
        service_name="frontend",
        operation="checkout.initiate",
        lookback="1h",
        limit=10,
    )

    assert fetched
    assert source.fetch_trace(fetched[0]["traceID"]) == [fetched[0]]


def test_zipkin_adapter_queries_v2_api_and_normalizes_trace() -> None:
    trace_id = "0123456789abcdef0123456789abcdef"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/traces"
        assert request.url.params["serviceName"] == "frontend"
        return httpx.Response(
            200,
            json=[
                [
                    {
                        "traceId": trace_id,
                        "id": "1111111111111111",
                        "name": "checkout",
                        "localEndpoint": {"serviceName": "frontend"},
                    }
                ]
            ],
        )

    source = ZipkinTraceSource(
        "http://zipkin.test", transport=httpx.MockTransport(handler)
    )
    traces = source.fetch_workflow_traces(
        service_name="frontend", operation="checkout", lookback="1h", limit=10
    )

    assert traces[0]["traceID"] == trace_id
    assert traces[0]["processes"]["p1"]["serviceName"] == "frontend"


def test_tempo_adapter_searches_then_fetches_otlp_trace() -> None:
    trace_id = "0123456789abcdef0123456789abcdef"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/search":
            assert "resource.service.name" in request.url.params["q"]
            return httpx.Response(200, json={"traces": [{"traceID": trace_id}]})
        assert request.url.path == f"/api/v2/traces/{trace_id}"
        return httpx.Response(
            200,
            json={
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "service.name",
                                    "value": {"stringValue": "frontend"},
                                }
                            ]
                        },
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": trace_id,
                                        "spanId": "1111111111111111",
                                        "name": "checkout",
                                    }
                                ]
                            }
                        ],
                    }
                ]
            },
        )

    source = TempoTraceSource(
        "http://tempo.test", transport=httpx.MockTransport(handler)
    )
    traces = source.fetch_workflow_traces(
        service_name="frontend", operation="checkout", lookback="1h", limit=10
    )

    assert traces[0]["traceID"] == trace_id
