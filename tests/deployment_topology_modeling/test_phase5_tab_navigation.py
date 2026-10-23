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
    assert '</span> Topology' in html

    assert 'id="tab-btn-intent"' in html
    assert 'data-target="tab-intent"' in html
    assert '</span> Declared vs live' in html

    assert 'id="tab-btn-drift"' in html
    assert 'data-target="tab-drift"' in html
    assert '</span> Exposed services' in html

    # The demo order: where pods run → what one node failure takes down → YAML vs cluster → experiments.
    order = [html.index(f'id="tab-btn-{tab}"') for tab in ("topology", "drift", "intent", "live-experiments", "output", "accuracy")]
    assert order == sorted(order)

    # The main output and the accuracy check are tabs of their own, not sections under every tab
    for tab in ("output", "accuracy"):
        assert f'id="tab-btn-{tab}"' in html
        assert f'id="tab-{tab}"' in html
    output_pane = html[html.index('id="tab-output"'):html.index('id="tab-accuracy"')]
    assert 'id="deployment-raw-json"' in output_pane
    assert 'data-role="output-snapshot"' in output_pane
    assert 'id="academic-panel"' in html[html.index('id="tab-accuracy"'):]

    # Dry-run scenarios are not shown on the dashboard (scripted, not evidence).
    assert 'id="tab-btn-scenarios"' not in html

    assert 'id="tab-btn-live-experiments"' in html
    assert 'data-target="tab-live-experiments"' in html
    assert '</span> Experiments' in html


def test_deployments_page_contains_all_tab_panes(client):
    response = client.get("/components/deployments")
    assert response.status_code == 200
    html = response.text

    # Verify tab panes exist
    assert 'id="tab-topology"' in html
    assert 'id="tab-intent"' in html
    assert 'id="tab-drift"' in html
    assert 'id="tab-scenarios"' not in html
    assert 'id="tab-live-experiments"' in html


def test_deployments_page_includes_phase5_scripts(client):
    response = client.get("/components/deployments")

    assert response.status_code == 200
    html = response.text

    assert "deployments_tab_router.js" in html
    assert "deployments_academic_widget.js" in html
    assert "deployments_topology_widget.js" in html
    assert "deployments_intent_widget.js" in html
    assert "deployments_drift_widget.js" in html
    assert "deployments_live_experiments_widget.js" in html
    assert "deployments_scenarios_widget.js" not in html
    assert "deployments_modal_manager.js" in html
