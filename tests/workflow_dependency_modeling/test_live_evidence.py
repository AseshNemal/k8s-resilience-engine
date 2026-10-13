from unittest.mock import patch

import pytest

from app.engine.workflow_dependency_modeling.auto_discovery import discover_root_workflows
from app.engine.workflow_dependency_modeling.service import get_live_evidence


def trace(tid, method, path, callee=None):
    spans = [{"traceID": tid, "spanID": "root", "processID": "p1",
              "operationName": "frontend", "references": [], "tags": [
                  {"key": "http.method", "value": method},
                  {"key": "http.target", "value": path}]}]
    if callee:
        spans.append({"traceID": tid, "spanID": "child", "processID": "p2",
                      "references": [{"refType": "CHILD_OF", "spanID": "root"}]})
    return {"traceID": tid, "spans": spans, "processes": {
        "p1": {"serviceName": "frontend"}, "p2": {"serviceName": callee}}}


def test_discovery_separates_requests_normalizes_products_excludes_health():
    traces = [trace("1", "GET", "/product/123?currency=USD"),
              trace("2", "GET", "/product/456"),
              trace("3", "POST", "/cart/checkout", "paymentservice"),
              trace("4", "GET", "/_healthz")]
    results = {r.operation_name: r.trace_count for r in discover_root_workflows(traces)}
    assert results == {"GET /product/{id}": 2, "POST /cart/checkout": 1}


@patch("app.engine.workflow_dependency_modeling.service.JaegerClient")
def test_live_evidence_filters_before_counting_and_does_not_infer_necessity(client):
    client.return_value.fetch_traces.return_value = [
        trace("1", "POST", "/cart/checkout", "paymentservice"),
        trace("2", "POST", "/cart/checkout"),
        trace("3", "GET", "/product/a", "productcatalogservice")]
    result = get_live_evidence("frontend", "POST /cart/checkout", "http://localhost:16686")
    assert result["traceCount"] == 2
    assert result["observedServices"] == ["frontend", "paymentservice"]
    assert result["criticality"] is None
    assert result["mandatoryServices"] == result["optionalServices"] == []
    assert result["serviceEvidence"][0]["frequency"] == 1
    assert result["serviceEvidence"][1]["frequency"] == .5
    assert result["edges"] == [{"source": "frontend", "target": "paymentservice"}]
    with pytest.raises(ValueError, match="No matching"):
        get_live_evidence("frontend", "POST /missing", "http://localhost:16686")


def test_live_evidence_uses_injected_trace_source() -> None:
    class Source:
        def fetch_workflow_traces(self, **query):
            assert query == {
                "service_name": "gateway",
                "operation": "",
                "limit": 25,
                "lookback": "30m",
            }
            return [trace("1", "POST", "/orders", "order-api")]

    result = get_live_evidence(
        "gateway",
        "POST /orders",
        "https://unused.invalid",
        25,
        "30m",
        Source(),
    )

    assert result["traceCount"] == 1
    assert result["observedServices"] == ["frontend", "order-api"]
