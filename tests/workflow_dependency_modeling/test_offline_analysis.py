"""Arbitrary application inputs drive results; fixture answers are never loaded."""

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workflows import router
from app.engine.workflow_dependency_modeling.service import analyze_workflow_evidence, get_live_evidence
from app.engine.workflow_dependency_modeling.auto_discovery import discover_root_workflows
from app.engine.workflow_dependency_modeling.trace_validator import validate_trace_quality


def trace(tid, *, service="gateway-x", operation="POST /deliveries", status=201, worker=True):
    spans = [{
        "spanID": "entry", "processID": "p1", "operationName": operation,
        "duration": 1500, "references": [],
        "tags": [{"key": "http.status_code", "value": status}],
    }]
    if worker:
        spans.append({"spanID": "child", "processID": "p2", "operationName": "dispatch",
                      "references": [{"refType": "CHILD_OF", "spanID": "entry"}]})
    return {"traceID": tid, "spans": spans, "processes": {
        "p1": {"serviceName": service}, "p2": {"serviceName": "dispatch-worker-x"},
    }}


def payload():
    return {
        "workflowDefinition": {
            "workflowId": "dispatch", "description": "Dispatch a delivery", "criticality": 0.65,
            "entryService": "gateway-x", "successCondition": {
                "protocol": "http", "terminalService": "gateway-x",
                "operationName": "POST /deliveries", "httpStatusCodes": [201],
            },
        },
        "operationName": "POST /deliveries",
        "traces": [trace("a"), trace("b", worker=False), trace("c", status=503)],
    }


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_success_cohort_drives_frequencies_and_graph_without_mutation():
    data = payload()
    before = deepcopy(data)
    result = analyze_workflow_evidence(data)
    assert data == before
    assert result.classification_trace_ids == ["a", "b"]
    assert result.failed_trace_count == 1
    assert result.candidate_graph.workflow_id == "dispatch"
    assert result.candidate_graph.criticality == pytest.approx(.65)
    assert result.candidate_graph.mandatory_services == ["gateway-x"]
    assert result.candidate_graph.optional_services == ["dispatch-worker-x"]
    assert result.service_evidence[0].frequency == pytest.approx(.5)
    assert result.edge_evidence[0]["frequency"] == pytest.approx(.5)


def test_thresholds_change_results_and_are_not_fixed_answers():
    data = {**payload(), "mandatoryThreshold": .5, "minEdgeFrequency": .75}
    result = analyze_workflow_evidence(data)
    assert result.candidate_graph.mandatory_services == ["dispatch-worker-x", "gateway-x"]
    assert result.candidate_graph.edges == []
    assert result.edge_evidence[0]["included"] is False


def test_quality_and_workflow_scope_do_not_contaminate_denominator():
    data = payload()
    bad = trace("bad")
    bad["spans"][0]["tags"] = [None]
    data["traces"] += [bad, trace("other-service", service="other-gateway"),
                       trace("other-operation", operation="GET /deliveries")]
    result = analyze_workflow_evidence(data)
    assert result.input_trace_count == 6
    assert result.matched_trace_count == 3
    assert result.unrelated_trace_count == 2
    assert result.trace_quality["invalidCount"] == 1
    assert result.service_evidence[0].frequency == pytest.approx(.5)


def test_missing_terminal_is_indeterminate_and_never_yields_a_fake_graph():
    data = payload()
    data["workflowDefinition"]["successCondition"]["terminalService"] = "not-instrumented"
    result = analyze_workflow_evidence(data)
    assert result.indeterminate_trace_count == 3
    assert result.candidate_graph is None
    assert result.classification_status == "insufficient_success_evidence"


def test_supplied_success_assertions_and_jaeger_envelope_are_used():
    data = payload()
    data["workflowDefinition"]["successCondition"]["maxDurationMs"] = 1
    data["traces"] = {"data": data["traces"]}
    result = analyze_workflow_evidence(data)
    assert result.failed_trace_count == 3
    assert result.candidate_graph is None


@pytest.mark.parametrize("change", [
    {"mandatoryThreshold": True}, {"mandatoryThreshold": -1}, {"minEdgeFrequency": "0.5"},
    {"operationName": " "}, {"traces": []}, {"traces": [None]}, {"extraAnswer": True},
])
def test_invalid_api_inputs_are_422(change):
    response = client().post("/api/workflows/analyze", json={**payload(), **change})
    assert response.status_code == 422, response.text


def test_api_returns_camel_case_graph_and_evidence():
    response = client().post("/api/workflows/analyze", json=payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["candidateGraph"]["workflowId"] == "dispatch"
    assert result["candidateGraph"]["mandatoryServices"] == ["gateway-x"]
    assert result["traceOutcomes"][2]["status"] == "failed"


@pytest.mark.parametrize("field,value", [("conditions", [None]), ("successCondition", [])])
def test_malformed_workflow_definition_is_a_validation_error(field, value):
    data = payload()
    data["workflowDefinition"][field] = value
    assert client().post("/api/workflows/analyze", json=data).status_code == 422


@pytest.mark.parametrize("source,raw", [
    ("zipkin", [[{"traceId": "a", "id": "b", "localEndpoint": {"serviceName": "api"}, "tags": 8}]]),
    ("vendor_tracing", {"resourceSpans": [None]}),
])
def test_malformed_adapter_payloads_are_validation_errors(source, raw):
    data = {**payload(), "sourceType": source, "traces": raw}
    assert client().post("/api/workflows/analyze", json=data).status_code == 422


def test_missing_duration_cannot_pass_a_duration_assertion():
    data = payload()
    data["workflowDefinition"]["successCondition"]["maxDurationMs"] = 5
    for raw in data["traces"]:
        raw["spans"][0].pop("duration")
    result = analyze_workflow_evidence(data)
    assert result.indeterminate_trace_count == 3
    assert result.candidate_graph is None


def test_zipkin_query_export_produces_application_specific_graph():
    data = payload()
    data["sourceType"] = "zipkin"
    data["traces"] = [[{
        "traceId": "a", "id": "entry", "name": "POST /deliveries",
        "localEndpoint": {"serviceName": "gateway-x"}, "kind": "SERVER",
        "duration": 1500, "timestamp": 1, "tags": {"http.status_code": "201"},
    }, {
        "traceId": "a", "id": "worker", "parentId": "entry", "name": "dispatch",
        "localEndpoint": {"serviceName": "dispatch-worker-x"}, "kind": "SERVER",
        "duration": 1000, "timestamp": 2,
    }]]
    result = analyze_workflow_evidence(data)
    assert result.successful_trace_count == 1
    assert result.candidate_graph.mandatory_services == ["dispatch-worker-x", "gateway-x"]


def test_analysis_does_not_read_fixture_answers(monkeypatch):
    from app.engine.workflow_dependency_modeling import fixture_repository

    def forbidden(*args, **kwargs):
        pytest.fail("Analysis must not read fixture answers")

    for name in ("get_scenario", "get_reference", "load_fixture_traces"):
        monkeypatch.setattr(fixture_repository, name, forbidden)
    assert analyze_workflow_evidence(payload()).candidate_graph.workflow_id == "dispatch"


def test_duplicate_trace_ids_cannot_inflate_the_success_cohort():
    data = payload()
    data["traces"].append(deepcopy(data["traces"][0]))
    result = analyze_workflow_evidence(data)
    assert result.trace_quality["invalidCount"] == 2
    assert result.classification_trace_ids == ["b"]
    assert result.candidate_graph.mandatory_services == ["gateway-x"]


def test_upload_page_renders_form_and_accessible_status():
    from app.web.workflows import router as page_router
    app = FastAPI()
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(page_router)
    response = TestClient(app).get("/components/workflows")
    assert response.status_code == 200
    assert 'data-role="offline-analysis-form"' in response.text
    assert 'id="offline-traces"' in response.text
    assert 'aria-live="polite" tabindex="-1"' in response.text


@pytest.mark.parametrize("key", ["mandatoryServices", "expectedServices", "edges"])
def test_answer_labels_are_rejected_as_inputs(key):
    data = payload()
    data["workflowDefinition"][key] = []
    assert client().post("/api/workflows/analyze", json=data).status_code == 422


@pytest.mark.parametrize("key,value", [
    ("tags", None), ("tags", [None]), ("references", [None]),
    ("references", {}), ("duration", -1), ("duration", "slow"),
    ("spanID", None), ("processID", []),
])
def test_malformed_nested_evidence_is_excluded_with_reasons(key, value):
    raw = trace("bad")
    raw["spans"][0][key] = value
    quality = validate_trace_quality([raw])
    assert quality.invalid_count == 1
    assert quality.results[0].reasons


def test_server_identity_wins_over_dns_alias():
    data = payload()
    raw = trace("a")
    raw["spans"].append({
        "spanID": "client", "processID": "p1", "operationName": "call-dispatch",
        "references": [{"refType": "CHILD_OF", "spanID": "entry"}], "tags": [
        {"key": "span.kind", "value": "client"},
        {"key": "server.address", "value": "dispatch.default.svc.cluster.local"},
    ]})
    raw["spans"][1]["references"][0]["spanID"] = "client"
    raw["spans"][1]["tags"] = [{"key": "span.kind", "value": "server"}]
    data["traces"] = [raw]
    result = analyze_workflow_evidence(data)
    assert result.trace_quality["validCount"] == 1
    assert result.candidate_graph.mandatory_services == ["dispatch-worker-x", "gateway-x"]
    assert [(edge.source, edge.target) for edge in result.candidate_graph.edges] == [
        ("gateway-x", "dispatch-worker-x"),
    ]
    assert result.edge_evidence[0]["totalCallCount"] == 1


def test_discovery_and_live_evidence_scope_same_operation_by_root_service():
    traces = [trace("a"), trace("b", service="different-gateway")]
    found = discover_root_workflows(traces)
    assert len(found) == 2
    assert {item.root_service for item in found} == {"gateway-x", "different-gateway"}

    class Source:
        def fetch_workflow_traces(self, **kwargs):
            return traces

    result = get_live_evidence("gateway-x", "POST /deliveries", "unused", trace_source=Source())
    assert result["traceCount"] == 1
    assert result["sampleTraceIds"] == ["a"]


def test_legacy_live_graph_requires_one_operation_and_scopes_its_evidence(monkeypatch):
    from app.engine.workflow_dependency_modeling import service
    traces = [trace("a"), trace("b", operation="GET /deliveries", worker=False)]

    class Source:
        def fetch_traces(self, **kwargs):
            # Normalized operation names need client-side matching against tags.
            assert "operation" not in kwargs
            return traces

    monkeypatch.setattr(service, "JaegerClient", lambda **kwargs: Source())
    with pytest.raises(ValueError, match="Select one"):
        service.get_live_workflow(service_name="gateway-x")
    graph = service.get_live_workflow(service_name="gateway-x", operation="GET /deliveries")
    assert graph.mandatory_services == ["gateway-x"]
    assert graph.edges == []


def test_invalid_fetched_evidence_remains_reported_in_live_view():
    valid = trace("a")
    invalid = trace("bad")
    invalid["spans"][0]["tags"] = [None]

    class Source:
        def fetch_workflow_traces(self, **kwargs):
            return [valid, invalid]

    result = get_live_evidence("gateway-x", "POST /deliveries", "unused", trace_source=Source())
    assert result["traceCount"] == 1
    assert result["inputTraceQuality"]["invalidCount"] == 1
