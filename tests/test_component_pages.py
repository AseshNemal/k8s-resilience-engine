from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


PAGE_CASES = [
    ("/", "Overview", "dashboard.css", "components/dashboard.js"),
    (
        "/components/workflows",
        "Workflow dependencies",
        "pages/workflows.css",
        "components/workflows.js",
    ),
    (
        "/components/deployments",
        "Deployment Topology Modeling",
        "pages/deployments.css",
        "components/deployments.js",
    ),
    (
        "/components/failure-impacts",
        "Failure Impact Prediction",
        "pages/failure_impacts.css",
        "components/failure_impacts.js",
    ),
    (
        "/components/recommendations",
        "Recommendation Evaluation and Validation",
        "pages/recommendations.css",
        "components/recommendations.js",
    ),
]


@pytest.mark.parametrize("path,title,css_file,js_file", PAGE_CASES)
def test_page_returns_success_with_title_and_assets(
    path: str, title: str, css_file: str, js_file: str
) -> None:
    response = client.get(path)

    assert response.status_code == 200
    body = response.text

    assert title in body
    assert css_file in body
    assert js_file in body


def test_dashboard_links_to_all_component_pages() -> None:
    response = client.get("/")

    assert response.status_code == 200
    body = response.text

    assert 'href="/components/workflows"' in body
    assert 'href="/components/deployments"' in body
    assert 'href="/components/failure-impacts"' in body
    assert 'href="/components/recommendations"' in body


def test_workflow_page_starts_with_user_inputs_and_saved_workflows() -> None:
    response = client.get("/components/workflows")
    assert response.status_code == 200
    body = response.text
    assert 'data-role="guided-analysis-form"' in body
    assert 'data-role="saved-workflows"' in body
    assert 'data-role="offline-analysis-form"' in body
    assert "Research Environment" not in body
    assert "Research audit controls" not in body
    assert "PP1" not in body
    assert "Synthetic Fixture Dataset" not in body
    assert 'value="http://localhost' not in body
    assert 'value="local-test"' not in body


def test_workflow_workspace_keeps_research_tools_out_of_the_user_flow() -> None:
    script = (
        REPOSITORY_ROOT / "app" / "static" / "js" / "components" / "workflows.js"
    ).read_text(encoding="utf-8")
    assert "/approval" in script
    assert "loadCampaignDecision" in script
    assert "local-demo" not in script
    assert "online_boutique" not in script
    assert "Component 3" not in script
    assert "seed-traces" not in script
    assert "benchmarkProfile" not in script


def test_workflow_workspace_keeps_find_and_trace_service_controls():
    body = client.get('/components/workflows').text
    assert 'Find workflows' in body
    assert 'data-role="trace-start-prompt" hidden' in body
    assert 'data-role="trace-start-confirm"' in body
    assert 'data-role="trace-stop-button" hidden' in body
    assert 'data-role="saved-workflows"' in body
    assert 'data-role="offline-analysis-form"' in body
