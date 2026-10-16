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
        "Workflow Dependency Modeling",
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


def test_workflow_page_uses_candidate_not_validated_necessity_language() -> None:
    response = client.get("/components/workflows")
    assert response.status_code == 200
    assert "High-frequency Candidates" in response.text


def test_workflow_page_explains_component_contract_and_stages() -> None:
    response = client.get("/components/workflows")
    assert response.status_code == 200
    assert "Build a workflow from real traces" in response.text
    assert "Choose a service and operation from recorded traces" in response.text
    assert "Get approved graph" in response.text
    assert 'data-workflow-stage-button="define"' in response.text
    assert 'data-workflow-stage-button="evidence"' in response.text
    assert 'data-workflow-stage-button="validate"' in response.text
    assert 'data-workflow-stage-button="evaluate"' in response.text
    assert 'data-workflow-stage="evidence" hidden' in response.text
    assert "Future Work" not in response.text
    assert "Lower-frequency Candidates" in response.text
    assert "Mandatory Services" not in response.text


def test_workflow_page_defaults_to_guided_analysis_not_internal_artifacts() -> None:
    response = client.get("/components/workflows")
    assert response.status_code == 200
    assert 'data-role="guided-analysis-form"' in response.text
    assert 'data-role="guided-discover-button"' in response.text
    assert 'data-role="guided-inspect-button"' in response.text
    assert "Build a workflow from real traces" in response.text
    assert "Recorded operation" in response.text
    assert "Trace backend" in response.text
    assert "Connect trace source" in response.text
    assert "Trace query address" in response.text
    assert "How dependency labels are decided" in response.text
    assert "Synthetic Fixture Dataset" in response.text
    assert "Connect a trace source or resume a saved experiment." in response.text
    assert "Research audit controls" in response.text
    assert '<details class="research-audit">' in response.text


def test_workflow_client_exposes_operator_gated_campaign_lifecycle() -> None:
    script = (
        REPOSITORY_ROOT / "app" / "static" / "js" / "components" / "workflows.js"
    ).read_text(encoding="utf-8")
    assert "Run local demo test · stop, test, restore" in script
    assert "Record service unavailable" in script
    assert "Classify dependencies" in script
    assert "Approve WorkflowGraph" in script
    assert "/approval" in script
    assert "loadCampaignDecision" in script
    assert "Campaign active" in script
    assert "No active campaign" in script
    assert 'benchmarkProfile: "online_boutique_checkout_v0_10_1"' not in script
    assert "I reviewed this execution definition and success contract" in script
    assert "Request options (JSON object)" in script
    assert "supportsGuidedExperiment" in script
    assert "updateGuidedSelectionAction" in script
    assert "The description documents your intent." in script
    assert "No workflow executions run automatically" in script
    assert "conditionEvidenceDetails" in script
    assert "guidedResult.replaceChildren()" in script
