import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


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
