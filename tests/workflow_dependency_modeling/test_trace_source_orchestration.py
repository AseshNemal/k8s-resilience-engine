"""Ensure saved non-Jaeger sources reach discovery and exact-trace polling."""

from types import SimpleNamespace

from app.engine.workflow_dependency_modeling import (
    analysis_session_service,
    campaign_execution_service,
    service,
)
from app.engine.workflow_dependency_modeling.trace_source import (
    TraceSourceConfiguration,
)


def test_analysis_discovery_uses_configured_generic_trace_source(monkeypatch) -> None:
    sentinel_source = object()
    received = {}

    monkeypatch.setattr(
        analysis_session_service,
        "create_trace_source",
        lambda configuration: sentinel_source,
    )

    def discover(**kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(service, "discover_live_workflows", discover)

    result = analysis_session_service.discover_analysis_workflows(
        {
            "traceSource": {
                "type": "zipkin",
                "endpoint": "http://localhost:9411",
                "authentication": {"type": "none"},
            },
            "entryService": "frontend",
            "lookback": "1h",
            "limit": 25,
        }
    )

    assert result == []
    assert received["trace_source"] is sentinel_source
    assert received["service_name"] == "frontend"


def test_campaign_exact_trace_polling_uses_saved_generic_source(monkeypatch) -> None:
    trace_id = "0123456789abcdef0123456789abcdef"
    received = []

    class Source:
        def fetch_trace(self, value):
            received.append(value)
            return [{"traceID": value, "spans": [], "processes": {}}]

    monkeypatch.setattr(
        campaign_execution_service,
        "create_trace_source",
        lambda configuration: Source(),
    )
    analysis = SimpleNamespace(
        trace_source=TraceSourceConfiguration.from_mapping(
            {
                "type": "tempo",
                "endpoint": "http://localhost:3200",
                "authentication": {"type": "none"},
            }
        )
    )

    traces = campaign_execution_service._default_trace_fetcher(analysis, trace_id)

    assert traces[0]["traceID"] == trace_id
    assert received == [trace_id]
