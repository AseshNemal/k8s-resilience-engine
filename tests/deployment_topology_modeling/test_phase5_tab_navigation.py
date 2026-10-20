"""
Tests for Phase 5 Multi-Tab Dashboard Navigation.
Component: Component 2 - Deployment Topology Modeling
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_deployments_page_renders_tab_navigation(client):
    response = client.get("/components/deployments")
    assert response.status_code == 200
    html = response.text

    # Verify tab navigation exists
    assert 'class="dashboard-tabs-nav"' in html
    assert 'role="tablist"' in html

    # Verify all 4 tab buttons exist with correct ARIA roles
    assert 'id="tab-btn-topology"' in html
    assert 'data-target="tab-topology"' in html
    assert 'Physical Topology Map' in html

    assert 'id="tab-btn-intent"' in html
    assert 'data-target="tab-intent"' in html
    assert 'Declared Intent Compliance' in html

    assert 'id="tab-btn-drift"' in html
    assert 'data-target="tab-drift"' in html
    assert 'Resilience Drift & SPOFs' in html

    assert 'id="tab-btn-scenarios"' in html
    assert 'data-target="tab-scenarios"' in html
    assert 'Chaos Runner & Markov Matrix' in html


def test_deployments_page_contains_all_tab_panes(client):
    response = client.get("/components/deployments")
    assert response.status_code == 200
    html = response.text

    # Verify tab panes exist
    assert 'id="tab-topology"' in html
    assert 'id="tab-intent"' in html
    assert 'id="tab-drift"' in html
    assert 'id="tab-scenarios"' in html


def test_deployments_page_includes_phase5_scripts(client):
    response = client.get("/components/deployments")

    assert response.status_code == 200
    html = response.text

    assert "deployments_tab_router.js" in html
    assert "deployments_academic_widget.js" in html
    assert "deployments_topology_widget.js" in html
    assert "deployments_intent_widget.js" in html
    assert "deployments_drift_widget.js" in html
    assert "deployments_scenarios_widget.js" in html
    assert "deployments_modal_manager.js" in html
