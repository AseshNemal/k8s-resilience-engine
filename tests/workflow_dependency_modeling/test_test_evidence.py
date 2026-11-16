"""Focused checks for trace-correlated test evidence and additive workflow editing."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from app.engine.workflow_dependency_modeling import management, test_discovery as discovery
from app.engine.workflow_dependency_modeling.workflow_probe_runner import run_http_workflow_probe
from app.shared.schemas.workflow_management import WorkflowEdit
from app.shared.schemas.workflow_test_evidence import TestEvidenceImport as EvidenceImport, HttpTestRun
from tests.workflow_dependency_modeling.test_workflow_management import editor, edit


TRACE = "a" * 32
NOW = datetime.now(timezone.utc)


def trace(identity=TRACE, timestamp=NOW):
    micros = int(timestamp.timestamp() * 1_000_000)
    return {"traceID": identity, "processes": {"p1": {"serviceName": "orders"}, "p2": {"serviceName": "payments"}}, "spans": [
        {"traceID": identity, "spanID": "0000000000000001", "processID": "p1", "operationName": "Submit", "startTime": micros, "duration": 100, "references": [], "tags": [{"key": "http.status_code", "type": "int64", "value": 200}]},
        {"traceID": identity, "spanID": "0000000000000002", "processID": "p2", "operationName": "Charge", "startTime": micros + 1, "duration": 50,
         "references": [{"refType": "CHILD_OF", "traceID": identity, "spanID": "0000000000000001"}]}
    ]}


def condition():
    return {"protocol": "http", "terminalService": "orders", "operationName": "Submit", "httpStatusCodes": [200]}


def execution(identity="checkout-1", trace_id=TRACE):
    return {"testName": "Existing checkout test", "executionId": identity, "runner": "pytest", "applicationUrl": "http://localhost:8080",
            "entryService": "orders", "startedAt": (NOW-timedelta(seconds=1)).isoformat(), "finishedAt": (NOW+timedelta(seconds=1)).isoformat(),
            "traceIds": [trace_id], "outcomeTraceId": trace_id, "reportedStatus": "passed", "assertions": [{"name": "Order confirmed", "passed": True}], "successCondition": condition()}


def bundle(**changes):
    return {"namespace": "shop", "connectionFingerprint": "fake-connection", "executions": [execution()], "traces": [trace()], **changes}


def ref(row):
    return {"kind": "test", "id": row["id"], "version": row["version"], "candidateId": row["candidates"][0]["candidateId"]}


def test_import_uses_exact_execution_ids_and_existing_success_evaluator(editor):
    _, _, client = editor
    response = client.post("/api/workflows/management/test-evidence/import", json=bundle(traces=[trace(), trace("b"*32)]))
    assert response.status_code == 200
    row = response.json(); candidate = row["candidates"][0]
    assert candidate["traceIds"] == [TRACE] and candidate["outcome"]["status"] == "succeeded"
    assert candidate["edges"][0]["dependency"] == "Unknown"
    assert candidate["edges"][0]["evidence"][0]["executionId"] == "checkout-1"
    assert "spans" not in str(row)  # raw potentially sensitive payloads are not stored
    body = edit(reviewed=True).model_dump(by_alias=True); body["evidenceRefs"] = [ref(row)]
    saved = client.post("/api/workflows/management/workflows", json=body).json()
    assert saved["edges"][0]["classificationBasis"] == "declared"
    assert saved["edges"][0]["interactionBasis"] == "observed"
    assert not saved["experimentallyValidated"]


@pytest.mark.parametrize("traces,reason", [([], "missing_trace"), ([trace(), trace()], "ambiguous_trace"), ([trace(timestamp=NOW-timedelta(hours=1))], "trace_outside_execution_window")])
def test_missing_or_unrelated_evidence_never_becomes_a_passing_workflow(editor, traces, reason):
    row = discovery.import_test_evidence(EvidenceImport.model_validate(bundle(traces=traces)))
    candidate = row["candidates"][0]
    assert not candidate["services"] and not candidate["edges"]
    assert candidate["outcome"]["status"] == "indeterminate"
    assert candidate["coverage"]["gaps"][0]["reason"] == reason


def test_test_runner_success_alone_is_not_workflow_validation(editor):
    e = execution(); e.pop("successCondition"); e.pop("outcomeTraceId")
    row = discovery.import_test_evidence(EvidenceImport.model_validate(bundle(executions=[e])))
    assert row["candidates"][0]["services"]
    assert row["candidates"][0]["outcome"]["status"] == "indeterminate"


def test_merge_preserves_all_sources_and_requires_review_of_conflicting_declarations(editor):
    repo, _, _ = editor
    e = execution(); e["dependencyDeclarations"] = [{"source": "orders", "target": "payments", "dependency": "Mandatory"}]
    first = discovery.import_test_evidence(EvidenceImport.model_validate(bundle(executions=[e])))
    second = discovery.import_test_evidence(EvidenceImport.model_validate(bundle(executions=[execution("checkout-2", "b"*32)], traces=[trace("b"*32)])))
    recorded = repo.append("recording", "recorded", 0, {"namespace": "shop", "connectionFingerprint": "fake-connection", "state": "stopped", "candidates": [
        {"candidateId": "selected", "name": "Selected operation", "edges": [{"source": "orders", "target": "payments", "evidence": [{"traceId": "recording-trace"}]}]},
        {"candidateId": "unrelated", "name": "Other operation", "edges": [{"source": "orders", "target": "payments", "evidence": [{"traceId": "do-not-attach"}]}]}
    ]})
    refs = [ref(first), ref(second), {"kind": "recording", "id": recorded["id"], "version": 1, "candidateId": "selected"}]
    body = edit(edges=[{"source": "orders", "target": "payments", "dependency": "Optional"}]).model_dump(by_alias=True)
    body.update(evidenceRefs=refs, recordingIds=["recorded"])
    draft = management.save_workflow(WorkflowEdit.model_validate(body))
    assert len(draft["edges"][0]["evidence"]) == 3
    assert "do-not-attach" not in str(draft)
    assert draft["classificationConflicts"] and draft["edges"][0]["dependency"] == "Optional"
    body.update(reviewed=True, expectedVersion=1)
    with pytest.raises(ValueError, match="conflicting"):
        management.save_workflow(WorkflowEdit.model_validate(body), draft["id"])
    r = refs[0]; body["edges"][0]["reviewedEvidence"] = [f"test:{r['id']}:{r['version']}:{r['candidateId']}"]
    reviewed = management.save_workflow(WorkflowEdit.model_validate(body), draft["id"])
    assert reviewed["version"] == 2 and len(reviewed["evidenceSources"]) == 3
    assert reviewed["graph"]["optionalServices"] == ["payments"]
    assert len(repo.versions("workflow", draft["id"])) == 2


def test_server_attested_classification_cannot_be_overwritten(editor):
    repo, _, _ = editor
    original = management.save_workflow(edit())
    original["edges"][0]["classificationBasis"] = "experimentally_validated"
    original["edges"][0]["evidence"] = [{"traceId":"verified-experiment-reference"}]
    repo.append("workflow", original["id"], 1, original)
    retained = management.save_workflow(edit(expectedVersion=2), original["id"])
    assert retained["edges"][0]["classificationBasis"] == "experimentally_validated"
    assert retained["edges"][0]["evidence"] == [{"traceId":"verified-experiment-reference"}]
    with pytest.raises(ValueError, match="validated classification"):
        management.save_workflow(edit(expectedVersion=3, edges=[{"source":"orders","target":"payments","dependency":"Optional"}]), original["id"])


def test_http_probe_reuses_trace_propagation_and_refresh_never_reruns_it(editor, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.headers["traceparent"].split("-")[1]); return httpx.Response(200, text="Order confirmed")
    def run(url, definition, **kwargs):
        return run_http_workflow_probe(url, definition, transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(discovery, "run_http_workflow_probe", run)
    class Traces:
        def fetch_trace(self, trace_id):
            return [trace(trace_id, datetime.now(timezone.utc))]
    monkeypatch.setattr(discovery.JaegerTraceSource, "from_url", lambda _: Traces())
    definition = {"runnerType": "http_sequence", "steps": [{"name":"open", "method":"GET", "path":"/", "expectedStatusCodes":[200], "responseContains":["Order confirmed"]}, {"name":"submit", "method":"POST", "path":"/checkout", "expectedStatusCodes":[200]}], "measuredStep":"submit", "successCondition":condition()}
    row = discovery.run_http_test(HttpTestRun(namespace="shop", connection_fingerprint="fake-connection", test_name="Existing HTTP probe", application_url="http://localhost", entry_service="orders", jaeger_url="http://jaeger:16686", execution_definition=definition))
    assert len(set(calls)) == 2
    assert row["candidates"][0]["outcome"]["status"] == "succeeded"
    monkeypatch.setattr(discovery, "run_http_workflow_probe", lambda *a, **k: pytest.fail("Refresh must never rerun a test"))
    refreshed = discovery.refresh_test_evidence(row["id"])
    assert refreshed["id"] == row["id"] and refreshed["version"] == 2
    assert len(management.list_documents("test")) == 1


def test_import_refuses_wrong_namespace_unknown_service_and_forged_validation(editor):
    _, _, client = editor
    body = bundle(); body["executions"][0]["experimentallyValidated"] = True
    assert client.post("/api/workflows/management/test-evidence/import", json=body).status_code == 422
    with pytest.raises(ValueError, match="real entry service"):
        discovery.import_test_evidence(EvidenceImport.model_validate(bundle(executions=[{**execution(), "entryService":"invented"}])))
    with pytest.raises(ValueError, match="separate trace IDs"):
        EvidenceImport.model_validate(bundle(executions=[execution(), execution("other")]))


def test_refresh_keeps_references_when_trace_retention_changes(editor, monkeypatch):
    e = execution(); e["traceIds"] = [TRACE, "b"*32, "c"*32]
    first = discovery.import_test_evidence(EvidenceImport.model_validate(bundle(executions=[e], traces=[trace(), trace("b"*32)], jaegerUrl=None)))
    class ChangedTraces:
        def fetch_trace(self, identity):
            return [] if identity == TRACE else [trace(identity)]
    monkeypatch.setattr(discovery.JaegerTraceSource, "from_url", lambda _: ChangedTraces())
    # Bind a query endpoint as a server-owned evidence revision, then refresh.
    repo = management.repository()
    configured = repo.append("test", first["id"], 1, {**first, "jaegerUrl": "http://jaeger:16686"})
    refreshed = discovery.refresh_test_evidence(configured["id"])
    candidate = refreshed["candidates"][0]
    assert set(candidate["traceIds"]) == {TRACE, "b"*32, "c"*32}
    assert len(candidate["edges"][0]["evidence"]) == 3
    assert candidate["coverage"]["retainedTraceIds"] == [TRACE]
    assert candidate["outcome"]["status"] == "indeterminate"



def test_legacy_recording_references_survive_a_new_scoped_merge(editor):
    repo, _, _ = editor
    candidates = [{"candidateId": name, "name": name, "edges": [{"source": "orders", "target": "payments", "evidence": [{"recordingId":"legacy", "traceId":name}]}]} for name in ("selected", "earlier-operation")]
    repo.append("recording", "legacy", 0, {"namespace":"shop", "connectionFingerprint":"fake-connection", "state":"stopped", "candidates": candidates})
    original = management.save_workflow(edit(recordingIds=["legacy"]))
    body = edit(expectedVersion=1, recordingIds=["legacy"]).model_dump(by_alias=True)
    body["evidenceRefs"] = [{"kind":"recording", "id":"legacy", "version":1, "candidateId":"selected"}]
    updated = management.save_workflow(WorkflowEdit.model_validate(body), original["id"])
    assert {e["traceId"] for e in updated["edges"][0]["evidence"]} == {"selected", "earlier-operation"}
    assert len(repo.versions("workflow", original["id"])) == 2
