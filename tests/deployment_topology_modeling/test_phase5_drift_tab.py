"""
Tests for Phase 5 Resilience Drift & Signals Tab.
Component: Component 2 - Deployment Topology Modeling
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_drift_tab_markup_presence(client):
    response = client.get("/components/deployments")
    assert response.status_code == 200
    html = response.text

    assert 'id="tab-drift"' in html
    assert "Which services would one node failure take down?" in html
    assert 'id="spof-banner-badge"' in html
    assert 'id="spof-banner-count"' in html

    # Category counters
    assert 'id="count-scale-drift"' in html
    assert 'id="count-affinity-drift"' in html
    assert 'id="count-resource-drift"' in html
    assert 'id="count-contention-drift"' in html
    assert 'id="count-spread-drift"' in html

    # Lists
    assert 'id="spof-workloads-list"' in html
    assert 'id="node-risk-rankings-list"' in html


def test_drift_categories_api_endpoint(client):
    response = client.get("/api/deployments/drift/categories")
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "scale_drift" in data["summary"]
    assert "node_affinity_drift" in data["summary"]
    assert "resource_limit_drift" in data["summary"]
    assert "resource_contention" in data["summary"]
    assert "topology_spread_violations" in data["summary"]


def test_drift_spofs_api_endpoint(client):
    response = client.get("/api/deployments/drift/spofs")
    assert response.status_code == 200
    spofs = response.json()
    assert isinstance(spofs, list)
    if spofs:
        assert "service_name" in spofs[0]
        assert "replica_concentration" in spofs[0]
