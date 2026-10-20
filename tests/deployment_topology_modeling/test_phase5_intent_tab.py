"""
Tests for Phase 5 Declared Intent vs Live Compliance Tab.
Component: Component 2 - Deployment Topology Modeling
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app


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


def test_manifest_compliance_api_endpoint(client):
    response = client.get("/api/deployments/manifests/compliance")
    assert response.status_code == 200
    data = response.json()
    assert "constraint_compliance_rate" in data
    assert 0.0 <= data["constraint_compliance_rate"] <= 1.0
