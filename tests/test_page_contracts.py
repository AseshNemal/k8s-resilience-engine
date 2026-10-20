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
