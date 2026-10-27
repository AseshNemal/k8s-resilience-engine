"""Setup suggestions are read from the recorded root requests, with their counts, and nothing else is filled in."""

import pytest

from app.engine.workflow_dependency_modeling.observed_request_evidence import (
    GATEWAY_SETTING,
    summarize_root_requests,
)


def trace(trace_id, status, *, error=False, duration=2000, host="localhost", port=18080):
    tags = [{"key": "http.method", "value": "POST"}, {"key": "http.target", "value": "/orders"},
            {"key": "http.status_code", "value": status}, {"key": "http.scheme", "value": "http"},
            {"key": "net.host.name", "value": host}, {"key": "net.host.port", "value": port},
            {"key": "span.kind", "value": "server"}]
    if error:
        tags.append({"key": "otel.status_code", "value": "ERROR"})
    return {"traceID": trace_id, "processes": {"p1": {"serviceName": "web"}, "p2": {"serviceName": "orders"}},
            "spans": [
                {"traceID": trace_id, "spanID": f"{trace_id}a", "operationName": "web", "references": [],
                 "startTime": 1, "duration": duration, "tags": tags, "processID": "p1"},
                {"traceID": trace_id, "spanID": f"{trace_id}b", "operationName": "Create",
                 "references": [{"refType": "CHILD_OF", "traceID": trace_id, "spanID": f"{trace_id}a"}],
                 "startTime": 2, "duration": 500, "tags": [], "processID": "p2"},
            ]}


def test_status_codes_are_counted_and_only_error_free_codes_below_400_are_suggested(monkeypatch):
    monkeypatch.delenv(GATEWAY_SETTING, raising=False)
    evidence = summarize_root_requests([
        trace("t1", 200), trace("t2", 200, duration=4000), trace("t3", 302),
        trace("t4", 500, error=True), trace("t5", 201, error=True),
    ])

    assert evidence["requestCount"] == 5
    assert evidence["statusCodes"][0] == {"code": "200", "count": 2, "withSpanError": 0}
    # 201 was below 400 but its request carried an error status, so it is not offered as success.
    assert evidence["suggestedSuccessStatusCodes"] == [200, 302]
    assert evidence["durationMs"] == {"min": pytest.approx(2.0), "median": pytest.approx(2.0), "max": pytest.approx(4.0)}
    assert evidence["suggestedApplicationUrl"]["url"] == "http://localhost:18080"
    assert evidence["suggestedApplicationUrl"]["basis"] == "5 of 5 recorded requests were received at this address"
    assert "request body and form fields" in evidence["notRecorded"]


def test_a_loopback_address_is_offered_through_the_backend_gateway_with_the_reason(monkeypatch):
    monkeypatch.setenv(GATEWAY_SETTING, "host.docker.internal")
    suggestion = summarize_root_requests([trace("t1", 200)])["suggestedApplicationUrl"]
    assert suggestion["recordedUrl"] == "http://localhost:18080"
    assert suggestion["url"] == "http://host.docker.internal:18080"
    assert GATEWAY_SETTING in suggestion["note"]


def test_a_session_saved_before_the_evidence_existed_serialises_exactly_as_before():
    from app.engine.workflow_dependency_modeling.analysis_session_models import WorkflowAnalysisSession

    # Experiment records freeze this mapping and compare it exactly; a new key would invalidate every saved experiment.
    saved = {
        "sessionId": "analysis:old", "environmentId": "test-env", "evidenceTier": "experimental",
        "traceSource": {"type": "jaeger", "endpoint": "http://jaeger.test:16686", "authentication": {"type": "none"}},
        "jaegerUrl": "http://jaeger.test:16686", "entryService": "web", "operationName": "POST /orders",
        "lookback": "1h", "workflowId": "post_orders", "traceCount": 2, "observedServices": ["orders", "web"],
        "serviceEvidence": [], "traceQuality": {"status": "VALID"}, "edges": [], "maturity": "observed",
        "nextAction": "confirm_success_condition", "limitations": [], "requiredActions": [],
        "createdAt": "2026-10-24T10:00:00+00:00", "createdBy": "researcher",
    }
    session = WorkflowAnalysisSession.from_mapping(saved)
    assert session.to_mapping() == WorkflowAnalysisSession.from_mapping(session.to_mapping()).to_mapping()
    assert "rootRequestEvidence" not in session.to_mapping()
    with_evidence = WorkflowAnalysisSession.from_mapping({**saved, "rootRequestEvidence": {"requestCount": 2}})
    assert with_evidence.to_mapping()["rootRequestEvidence"] == {"requestCount": 2}


def test_nothing_is_suggested_when_the_traces_do_not_record_it(monkeypatch):
    monkeypatch.delenv(GATEWAY_SETTING, raising=False)
    bare = trace("t1", 200)
    bare["spans"][0]["tags"] = [{"key": "span.kind", "value": "server"}]
    evidence = summarize_root_requests([bare])
    assert evidence["statusCodes"] == [{"code": "unrecorded", "count": 1, "withSpanError": 0}]
    assert evidence["suggestedSuccessStatusCodes"] == []
    assert evidence["suggestedApplicationUrl"] is None
