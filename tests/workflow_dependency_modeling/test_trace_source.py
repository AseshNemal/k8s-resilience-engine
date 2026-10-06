"""Tests for the replaceable trace evidence source boundary."""

from typing import Any

import pytest

from app.engine.workflow_dependency_modeling.trace_source import (
    JaegerTraceSource,
    TraceSourceConfiguration,
)


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
