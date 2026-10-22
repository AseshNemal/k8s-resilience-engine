"""
The Deployment Topology page shows one namespace, chosen in its header, for every tab.
Component: Component 2 - Deployment Topology Modeling
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

JS = Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "components"


@pytest.fixture
def client():
    return TestClient(app)


def test_page_has_a_namespace_selector(client):
    html = client.get("/components/deployments").text
    assert 'id="deployment-namespace"' in html
    assert 'data-role="namespace-select"' in html
    assert '<label class="namespace-picker" for="deployment-namespace">' in html


def test_namespace_script_loads_before_the_widgets_that_use_it(client):
    html = client.get("/components/deployments").text
    tag = lambda name: html.index(f"/js/components/{name}?v=")  # noqa: E731 - the <script src=...> tag
    for widget in ("deployments_intent_widget.js", "deployments_drift_widget.js",
                   "deployments_modal_manager.js", "deployments.js"):
        assert tag("deployments_namespace.js") < tag(widget), widget


@pytest.mark.parametrize("script, endpoints", [
    ("deployments_intent_widget.js", ["/api/deployments/manifests\"", "/api/deployments/manifests/compliance"]),
    ("deployments_drift_widget.js", ["/api/deployments/drift/categories", "/api/deployments/drift/spofs",
                                     "/api/deployments/drift/nodes/risk"]),
    ("deployments_modal_manager.js", ["/api/deployments/drift/exposure/"]),
])
def test_every_cluster_request_carries_the_chosen_namespace(script, endpoints):
    source = (JS / script).read_text(encoding="utf-8")
    for endpoint in endpoints:
        line = next(line for line in source.splitlines() if endpoint in line)
        assert "scoped(" in line, f"{script}: {endpoint} is requested without the page's namespace"


def test_page_controller_scopes_discovery_and_lists_namespaces():
    source = (JS / "deployments.js").read_text(encoding="utf-8")
    assert "scope().withNamespace(LIVE_ENDPOINT)" in source
    assert '"/api/deployments/discovery/all"' in source


@pytest.mark.parametrize("path", [
    "/api/deployments/manifests?namespace=default",
    "/api/deployments/manifests/compliance?namespace=default",
    "/api/deployments/drift/categories?namespace=default",
    "/api/deployments/drift/spofs?namespace=default",
    "/api/deployments/drift/nodes/risk?namespace=default",
])
def test_the_routes_the_page_scopes_accept_a_namespace(client, path):
    assert client.get(path).status_code == 200


def test_intent_table_matches_deployments_to_their_live_service():
    # Bookinfo's Deployment reviews-v1 backs Service reviews: the table matches through the
    # deploymentNames the backend resolved from owner references, not only by equal names.
    source = (JS / "deployments_intent_widget.js").read_text(encoding="utf-8")
    assert "deploymentNames ?? s.deployment_names" in source
    assert "const liveMap = buildLiveMap(liveServices);" in source


def test_page_shows_where_and_when_its_data_was_read(client):
    html = client.get("/components/deployments").text
    assert 'data-role="evidence-box"' in html
    for role in ("evidence-namespace", "evidence-time", "evidence-snapshot", "evidence-took", "evidence-command"):
        assert f'data-role="{role}"' in html
    source = (JS / "deployments.js").read_text(encoding="utf-8")
    assert "renderEvidence(body);" in source
    assert "get pods -o wide" in source
