"""
Tests for Phase 5 Physical Topology Tab.
Component: Component 2 - Deployment Topology Modeling
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_topology_tab_structure(client):
    response = client.get("/components/deployments")
    assert response.status_code == 200
    html = response.text

    assert 'id="tab-topology"' in html
    assert "Physical Cluster & Failure Domain Topology" in html
    assert 'id="filter-topology-zone"' in html
    assert 'id="deployment-zone-grid"' in html
    assert 'id="deployment-concentration-panel"' in html
    assert "Replica Concentration" in html


def test_topology_nodes_api_endpoint(client):
    response = client.get("/api/deployments/topology/nodes")
    assert response.status_code == 200
    nodes = response.json()
    assert isinstance(nodes, list)
    assert len(nodes) >= 1
    assert "name" in nodes[0]
    assert "zone" in nodes[0]


def test_topology_zones_api_endpoint(client):
    response = client.get("/api/deployments/topology/zones")
    assert response.status_code == 200
    zones = response.json()
    assert isinstance(zones, list)
    assert len(zones) >= 1
    assert "zone" in zones[0]
    assert "node_count" in zones[0]
