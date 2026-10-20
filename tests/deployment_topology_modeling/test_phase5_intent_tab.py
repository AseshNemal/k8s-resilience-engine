"""
Tests for Phase 5 Declared Intent vs Live Compliance Tab.
Component: Component 2 - Deployment Topology Modeling
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.engine.deployment_topology_modeling import service


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
    assert src["found"] is True
    assert src["path"] == "deploy/test-workloads"


def _point_sources(monkeypatch, default, fallback):
    monkeypatch.setattr(service, "DEFAULT_MANIFEST_PATH", default)
    monkeypatch.setattr(service, "FALLBACK_MANIFEST_DIR", fallback)


def test_resolver_prefers_default_manifest(monkeypatch, tmp_path):
    default = tmp_path / "boutique.yaml"
    default.write_text("")
    _point_sources(monkeypatch, default, tmp_path / "fallback")
    assert service.resolve_manifest_source() == default
    assert service.get_manifest_source()["is_default"] is True


def test_resolver_falls_back_when_default_missing(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    _point_sources(monkeypatch, tmp_path / "missing.yaml", fallback)
    assert service.resolve_manifest_source() == fallback
    assert service.get_manifest_source()["is_default"] is False


def test_resolver_reports_no_manifest(monkeypatch, tmp_path):
    _point_sources(monkeypatch, tmp_path / "missing.yaml", tmp_path / "missing-dir")
    assert service.resolve_manifest_source() is None
    assert service.get_manifest_source()["found"] is False
    assert service.get_manifest_intent() == []


def test_explicit_manifest_path_wins(monkeypatch, tmp_path):
    _point_sources(monkeypatch, tmp_path / "missing.yaml", tmp_path / "missing-dir")
    assert service.resolve_manifest_source("custom.yaml") == service.Path("custom.yaml")


def test_drift_analysis_and_intent_tab_read_the_same_manifest(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    _point_sources(monkeypatch, tmp_path / "missing.yaml", fallback)
    read_paths = []
    monkeypatch.setattr(service, "run_manifest_pipeline", lambda path: read_paths.append(path) or [])

    service._load_manifest_intents(None, [])
    service.get_manifest_intent()

    assert read_paths == [str(fallback), str(fallback)]


def test_manifest_compliance_api_endpoint(client):
    response = client.get("/api/deployments/manifests/compliance")
    assert response.status_code == 200
    data = response.json()
    assert "constraint_compliance_rate" in data
    assert 0.0 <= data["constraint_compliance_rate"] <= 1.0
