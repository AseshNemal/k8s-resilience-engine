"""Essential persistence, evidence and approval boundaries for the hybrid editor."""
import pytest
from fastapi.testclient import TestClient

from app.core.database import get_engine
from app.engine.workflow_dependency_modeling import management, service
from app.engine.recommendation_evaluation_validation.declared_workflow import get_declared_workflow
from app.main import app
from app.shared.evidence_errors import EvidenceError
from app.shared.persistence.models import Base
from app.shared.schemas.workflow_management import WorkflowEdit, RecordingStart
from tests.support.fake_cluster import FakeClusterSource, node, pod, deployment, service as kube_service, replicaset


@pytest.fixture
def editor(tmp_path, monkeypatch, use_cluster):
    engine = get_engine(f"sqlite:///{tmp_path / 'editor.db'}")
    Base.metadata.create_all(engine)
    repo = management.ManagementRepository(engine)
    monkeypatch.setattr(management, "repository", lambda: repo)
    monkeypatch.delenv("RESILIENCE_DECLARED_WORKFLOW_PATH", raising=False)
    cluster = use_cluster(FakeClusterSource(
        nodes=[node("worker-a"), node("worker-b")],
        pods={"shop": [pod("orders-1", "worker-a", app="orders"), pod("payments-1", "worker-b", app="payments")]},
        deployments={"shop": [deployment("orders", 1), deployment("payments", 1)]},
        services={"shop": [kube_service("orders", {"app": "orders"}), kube_service("payments", {"app": "payments"})]},
        replicasets={"shop": [replicaset("orders-rs", "orders"), replicaset("payments-rs", "payments")]},
    ))
    return repo, cluster, TestClient(app)


def edit(**changes):
    body = {"name": "Submit order", "namespace": "shop", "connectionFingerprint": "fake-connection",
            "expectedVersion": 0, "actor": "Researcher", "entryService": "orders", "criticality": 0.8,
            "nodes": [{"service": "orders", "x": 40, "y": 60}, {"service": "payments", "x": 300, "y": 60}],
            "edges": [{"source": "orders", "target": "payments", "dependency": "Mandatory"}], "reviewed": False}
    return WorkflowEdit.model_validate({**body, **changes})


def test_manual_workflow_needs_no_tracing_and_review_remains_declared(editor, monkeypatch):
    repo, _, client = editor
    monkeypatch.setattr(management.JaegerTraceSource, "from_url", lambda _: pytest.fail("Manual editing must not read traces"))
    first = client.post("/api/workflows/management/workflows", json=edit(reviewed=True).model_dump(by_alias=True))
    assert first.status_code == 200
    row = first.json()
    assert row["graph"]["mandatoryServices"] == ["orders", "payments"]
    assert row["experimentallyValidated"] is False
    declaration = get_declared_workflow(row["id"])
    assert declaration.source_name == "workflow-editor" and declaration.namespace == "shop"
    assert declaration.to_dict()["observed"] is False
    assert client.get(f"/api/workflows/management/workflows/{row['id']}/graph").json()["basis"] == "declared"
    assert repo.latest("workflow", row["id"])["version"] == 1


def test_unknown_and_unreachable_graphs_cannot_be_reviewed(editor):
    for changes in [{"edges": [{"source": "orders", "target": "payments", "dependency": "Unknown"}]},
                    {"edges": []}, {"criticality": None}]:
        with pytest.raises(ValueError):
            management.save_workflow(edit(reviewed=True, **changes))
    row = management.save_workflow(edit(edges=[{"source": "orders", "target": "payments", "dependency": "Unknown"}]))
    assert row["graph"] is None


def test_conflicting_saves_and_delete_preserve_all_versions(editor):
    repo, _, _ = editor
    first = management.save_workflow(edit())
    second = management.save_workflow(edit(name="Revised", expectedVersion=1), first["id"])
    with pytest.raises(EvidenceError, match="WORKFLOW_VERSION_CONFLICT"):
        management.save_workflow(edit(expectedVersion=1), first["id"])
    deleted = management.delete_workflow(first["id"], 2, "Researcher")
    assert deleted["deleted"] and deleted["version"] == 3
    assert [row["name"] for row in repo.versions("workflow", first["id"])] == ["Submit order", second["name"], second["name"]]
    assert management.reviewed_workflows() == []


def test_cluster_namespace_and_missing_service_checks_refuse_rebinding(editor):
    with pytest.raises(EvidenceError, match="WORKFLOW_CLUSTER_CHANGED"):
        management.save_workflow(edit(connectionFingerprint="other-cluster"))
    with pytest.raises(EvidenceError, match="WORKFLOW_SERVICES_UNAVAILABLE"):
        management.save_workflow(edit(nodes=[{"service": "invented", "x": 1, "y": 1}], edges=[], entryService="invented"))
    first = management.save_workflow(edit())
    with pytest.raises(EvidenceError):
        management.save_workflow(edit(namespace="other", expectedVersion=1), first["id"])


def test_optional_parent_does_not_make_its_mandatory_child_workflow_mandatory(editor):
    graph = management._graph(edit(edges=[{"source": "orders", "target": "payments", "dependency": "Optional"}], reviewed=True), "id")
    assert graph.mandatory_services == ["orders"]
    assert graph.optional_services == ["payments"]


def test_recording_has_fixed_window_and_preserves_call_provenance(editor, monkeypatch):
    repo, _, _ = editor
    class Traces:
        traces = []
        def list_services(self):
            return ["orders", "payments", "unrelated"]
        def fetch_trace_window(self, service_name, start_us, end_us, limit):
            return self.traces
    source = Traces()
    monkeypatch.setattr(management.JaegerTraceSource, "from_url", lambda _: source)
    start = management.start_recording(RecordingStart(namespace="shop", connection_fingerprint="fake-connection", jaeger_url="http://jaeger:16686"))
    timestamp = start["startedMicros"]
    source.traces = [{"traceID": "abc123", "processes": {"p1": {"serviceName": "orders"}, "p2": {"serviceName": "payments"}}, "spans": [
        {"traceID": "abc123", "spanID": "a", "processID": "p1", "operationName": "Submit", "startTime": timestamp, "duration": 100, "references": []},
        {"traceID": "abc123", "spanID": "b", "processID": "p2", "operationName": "Charge", "startTime": timestamp + 1, "duration": 50,
         "references": [{"refType": "CHILD_OF", "traceID": "abc123", "spanID": "a"}]},
    ]}]
    stopped = management.stop_recording(start["id"])
    assert stopped["state"] == "stopped" and stopped["candidates"]
    assert stopped["coverage"]["tracesFetched"] == 1  # queries deduplicate traces
    assert stopped["candidates"][0]["edges"][0]["dependency"] == "Unknown"
    row = management.save_workflow(edit(recordingIds=[start["id"]], reviewed=True))
    edge = row["edges"][0]
    assert edge["classificationBasis"] == "declared" and edge["interactionBasis"] == "observed"
    assert edge["evidence"][0]["traceId"] == "abc123"
    assert row["experimentallyValidated"] is False
    again = management.stop_recording(start["id"])
    assert again["endedMicros"] == stopped["endedMicros"]
    assert len(repo.versions("recording", start["id"])) == 4


def test_client_cannot_supply_proof_or_trace_evidence(editor):
    _, _, client = editor
    body = edit().model_dump(by_alias=True)
    body["experimentallyValidated"] = True
    assert client.post("/api/workflows/management/workflows", json=body).status_code == 422
    body.pop("experimentallyValidated")
    body["edges"][0]["evidence"] = [{"traceId": "fabricated"}]
    assert client.post("/api/workflows/management/workflows", json=body).status_code == 422
