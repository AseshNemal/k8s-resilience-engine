"""
Tests for Phase 5 Declared Intent vs Live Compliance Tab.
Component: Component 2 - Deployment Topology Modeling
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.engine.deployment_topology_modeling import service
from app.engine.deployment_topology_modeling.manifest_parsing.manifest_models import DeclaredIntent


@pytest.fixture
def client():
    return TestClient(app)


def test_intent_tab_markup_presence(client):
    response = client.get("/components/deployments")
    assert response.status_code == 200
    html = response.text

    assert 'id="tab-intent"' in html
    assert "Declared Manifest Intent vs Live Placement Compliance" in html
    assert 'id="compliance-overall-pill"' in html
    assert 'id="compliance-rate-val"' in html
    assert 'id="intent-comparison-table"' in html
    assert 'id="intent-table-body"' in html
    assert "Declared Replicas (YAML)" in html
    assert "Live Ready Replicas" in html


def test_manifest_intent_api_endpoint(client):
    response = client.get("/api/deployments/manifests")
    assert response.status_code == 200
    intents = response.json()
    assert isinstance(intents, list)
    assert len(intents) >= 1
    assert "service_name" in intents[0]


def test_intent_tab_shows_manifest_source_note(client):
    html = client.get("/components/deployments").text
    assert 'data-role="manifest-source"' in html


def test_manifest_source_api_endpoint(client):
    # conftest points the default manifest at the tracked deploy/test-workloads folder.
    response = client.get("/api/deployments/manifests/source")
    assert response.status_code == 200
    src = response.json()
    assert src["source"] == "manifest"
    assert src["path"] == "deploy/test-workloads"


def test_source_is_the_repository_manifest_when_present(monkeypatch, tmp_path):
    default = tmp_path / "boutique.yaml"
    default.write_text("")
    monkeypatch.delenv(service.MANIFEST_PATH_ENV, raising=False)
    monkeypatch.setattr(service, "DEFAULT_MANIFEST_PATH", default)
    assert service.get_manifest_source() == {"source": "manifest", "path": "boutique.yaml", "found": True}


def test_source_is_the_cluster_when_no_manifest_exists(monkeypatch, tmp_path):
    monkeypatch.delenv(service.MANIFEST_PATH_ENV, raising=False)
    monkeypatch.setattr(service, "DEFAULT_MANIFEST_PATH", tmp_path / "missing.yaml")
    assert service.get_manifest_source() == {"source": "cluster", "path": None, "found": True}


def test_operator_named_manifest_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(service.MANIFEST_PATH_ENV, str(tmp_path / "named"))
    monkeypatch.setattr(service, "DEFAULT_MANIFEST_PATH", tmp_path / "boutique.yaml")
    src = service.get_manifest_source()
    assert src["source"] == "manifest"
    assert src["path"] == "named"
    assert src["found"] is False  # a named source that does not exist is reported, never skipped


def test_drift_analysis_and_intent_tab_read_the_same_manifest(monkeypatch, tmp_path):
    named = tmp_path / "named"
    named.mkdir()
    monkeypatch.setenv(service.MANIFEST_PATH_ENV, str(named))
    read_paths = []

    def fake_pipeline(path):
        read_paths.append(path)
        return [DeclaredIntent(service_name="paymentservice")]

    monkeypatch.setattr(service, "run_manifest_pipeline", fake_pipeline)
    service._load_manifest_intents(None, [])
    service.get_manifest_intent()

    assert read_paths == [str(named), str(named)]


def test_manifest_compliance_api_endpoint(client):
    response = client.get("/api/deployments/manifests/compliance")
    assert response.status_code == 200
    data = response.json()
    assert "constraint_compliance_rate" in data
    assert 0.0 <= data["constraint_compliance_rate"] <= 1.0
