"""The deployments page opens with one verdict banner that carries the headline metrics."""
from fastapi.testclient import TestClient

from app.main import app


def test_verdict_banner_holds_the_headline_metrics_before_the_tabs():
    html = TestClient(app).get("/components/deployments").text
    assert 'data-role="verdict"' in html
    for role in ("verdict-icon", "verdict-namespace", "verdict-title", "verdict-text"):
        assert f'data-role="{role}"' in html
    for metric in ("deployment-node-count", "deployment-service-count", "deployment-ready-pods", "deployment-exposed-services"):
        assert f'data-metric="{metric}"' in html
    assert html.index('data-role="verdict"') < html.index('class="dashboard-tabs-nav"')
