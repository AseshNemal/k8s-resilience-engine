"""Each page script may only look for elements its template (or the shared layout) actually provides.

Page scripts cannot be run in a browser here, so this catches the commonest silent failure: a script that
queries a ``data-role`` or id that a template edit removed. It reads files only.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
SCRIPTS = ROOT / "app" / "static" / "js" / "components"

PAGES = {
    "dashboard": (TEMPLATES / "dashboard.html", SCRIPTS / "dashboard.js"),
    "deployments": (TEMPLATES / "components" / "deployments.html", SCRIPTS / "deployments.js"),
    "failure_impacts": (TEMPLATES / "components" / "failure_impacts.html", SCRIPTS / "failure_impacts.js"),
    "recommendations": (TEMPLATES / "components" / "recommendations.html", SCRIPTS / "recommendations.js"),
    "workflows": (TEMPLATES / "components" / "workflows.html", SCRIPTS / "workflows.js"),
}
SHARED_PARTIALS = [
    TEMPLATES / "base.html", TEMPLATES / "partials" / "topbar.html", TEMPLATES / "partials" / "sidebar.html",
    TEMPLATES / "partials" / "metric_card.html", TEMPLATES / "partials" / "raw_json_panel.html",
    TEMPLATES / "partials" / "loading_state.html",
]

client = TestClient(app)


def referenced(script: str) -> tuple[set[str], set[str]]:
    roles = set(re.findall(r"""\[data-(?:role|metric)=\\?["']([a-z0-9-]+)\\?["']\]""", script))
    ids = set(re.findall(r"""getElementById\(["']([^"']+)["']\)""", script)) | set(
        re.findall(r"""querySelector\(["']#([A-Za-z0-9_-]+)["']\)""", script)
    )
    return roles, ids


@pytest.mark.parametrize("page", sorted(PAGES))
def test_script_selectors_exist_in_the_template(page):
    template_path, script_path = PAGES[page]
    markup = template_path.read_text(encoding="utf-8") + "".join(p.read_text(encoding="utf-8") for p in SHARED_PARTIALS)
    roles, ids = referenced(script_path.read_text(encoding="utf-8"))

    if page == "recommendations":
        # Null-guarded compatibility hooks for the earlier single-workflow page. The current template deliberately
        # omits them; its all-workflows path is exercised by the frontend integration tests.
        roles -= {"rec-workflow-select", "rec-portfolio", "rec-step1-next"}

    missing_roles = sorted(r for r in roles if r not in markup)
    missing_ids = sorted(i for i in ids if i not in markup)

    assert missing_roles == [] and missing_ids == [], f"{page}: roles {missing_roles}, ids {missing_ids}"


def test_the_selector_scan_would_notice_a_removed_element():
    roles, ids = referenced("""region.querySelector('[data-role="gone"]'); document.getElementById("also-gone");""")
    assert roles == {"gone"} and ids == {"also-gone"}


# ---- the Member 4 page ------------------------------------------------------------------------------------------

def recommendations_page() -> str:
    response = client.get("/components/recommendations")
    assert response.status_code == 200
    return response.text


def test_recommendations_has_no_workflow_choice_or_portfolio_handoff():
    markup = recommendations_page()
    assert 'data-all-workflows="true"' in markup
    assert 'data-role="rec-workflow-select"' not in markup
    assert 'data-role="rec-portfolio"' not in markup


@pytest.mark.parametrize("marker", [
    'data-role="rec-candidate-list"',        # candidate cards
    'data-role="rec-candidate-table-body"',  # ranking
    'data-role="rec-placement-current"',     # current placement (real evidence only)
    'data-role="rec-placement-predicted"',   # predicted placement (engine-supplied only)
    'data-role="rec-validation"',            # predicted vs observed
    'data-role="rec-yaml"',                  # generated YAML proposal
    'id="approval-section"',                 # approval gate (the extension build patches this exact marker)
    'id="approval-modal"',
])
def test_recommendations_page_has_every_restored_section(marker):
    assert marker in recommendations_page()


def test_the_approval_marker_the_extension_build_relies_on_appears_exactly_once():
    assert recommendations_page().count('<div class="card" id="approval-section">') == 1


def test_display_components_load_before_the_page_script():
    page = recommendations_page()
    assert page.index("js/recommendation-render.js") < page.index("components/recommendations.js")


def test_the_topology_comparison_loads_before_the_page_script_and_has_somewhere_to_draw():
    page = recommendations_page()
    assert page.index("js/recommendation-topology.js") < page.index("components/recommendations.js")
    assert 'data-role="rec-topology"' in page and 'data-role="rec-topology-box"' in page


def test_the_page_never_offers_a_simulated_placement_of_its_own():
    page = recommendations_page()
    script = (SCRIPTS / "recommendations.js").read_text(encoding="utf-8")
    render = (ROOT / "app" / "static" / "js" / "recommendation-render.js").read_text(encoding="utf-8")
    assert "Sample Virtual Placement" not in page
    assert not re.search(r"%\s*\w*\.?(nodes|length)", script + render)


# ---- the Overview ----------------------------------------------------------------------------------------------

def test_overview_lists_every_namespace_and_has_no_namespace_box():
    response = client.get("/")

    assert response.status_code == 200
    assert 'data-role="overview-namespaces"' in response.text
    assert 'data-role="namespace"' not in response.text
    assert "sample data" not in response.text.lower()


def test_overview_script_reads_the_all_namespaces_endpoint_only():
    script = (SCRIPTS / "dashboard.js").read_text(encoding="utf-8")

    assert "/api/deployments/discovery/all" in script
    assert "discovery?namespace" not in script
    assert "describeOverview" in script


def test_overview_has_the_research_summary_and_reads_only_stored_or_live_results():
    response = client.get("/")
    script = (SCRIPTS / "dashboard.js").read_text(encoding="utf-8")
    summary = (ROOT / "app" / "static" / "js" / "overview-summary.js").read_text(encoding="utf-8")

    for role in ("overview-status", "overview-risk", "overview-applied", "overview-results", "overview-placement", "overview-evidence",
                 "overview-chip-cluster", "overview-chip-workflow", "overview-chip-prediction", "overview-chip-test"):
        assert f'data-role="{role}"' in response.text, role
    # the chip links are in the page, so the extension build can point them at its own files
    assert 'href="/components/failure-impacts"' in response.text
    # reads only: no write verb and no sample, demo or fixture source
    assert "method:" not in script and "POST" not in script
    assert not re.search(r"sample|demo|fixture|Math\.random", script + summary, re.I)


def test_overview_keeps_applications_and_topology_comparison_visible():
    markup = client.get("/").text
    assert 'id="overview-applications-title"' in markup
    assert 'overview-topology-comparison"' in markup
    assert '<details' not in (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
    assert markup.count('data-role="overview-placement"') == 1
    assert 'data-role="overview-matrix"' not in markup
