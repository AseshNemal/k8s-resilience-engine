"""Tests for the replaceable trace evidence source boundary."""

from typing import Any

from app.engine.workflow_dependency_modeling.trace_source import JaegerTraceSource


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
