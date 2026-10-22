"""The deployments page opens with one verdict banner that carries the headline metrics."""
from fastapi.testclient import TestClient

from app.main import app


def test_verdict_banner_holds_the_headline_metrics_before_the_tabs():
    html = TestClient(app).get("/components/deployments").text
    assert 'data-role="verdict"' in html
    for role in ("verdict-icon", "verdict-title", "verdict-text", "verdict-names"):
        assert f'data-role="{role}"' in html
    for metric in ("deployment-node-count", "deployment-service-count", "deployment-ready-pods", "deployment-exposed-services"):
        assert f'data-metric="{metric}"' in html
    assert html.index('data-role="verdict"') < html.index('class="dashboard-tabs-nav"')


def test_each_headline_number_opens_the_tab_behind_it():
    html = TestClient(app).get("/components/deployments").text
    for tab in ("topology", "intent", "drift"):
        assert f'data-goto-tab="{tab}"' in html
        assert f'id="tab-{tab}"' in html


def test_header_keeps_only_the_namespace_reload_and_live_status():
    html = TestClient(app).get("/components/deployments").text
    header = html[html.index('class="c2-header"'):html.index('data-role="verdict"')]
    for role in ("namespace-select", "reload-button", "source-badge", "evidence-box"):
        assert f'data-role="{role}"' in header
    assert "contract-chip" not in header
