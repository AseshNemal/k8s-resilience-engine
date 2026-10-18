"""Tests for the extension UI build: the pages rendered from the unmodified app, plus the Docker look."""

import filecmp
import re
from pathlib import Path

import pytest

from app.main import BASE_DIR
from extension import build_ui

UI_SRC = Path(__file__).resolve().parents[2] / "extension" / "ui-src"
PAGE_FILES = ["index.html", "workflows.html", "deployments.html", "failure-impacts.html", "recommendations.html", "connect.html"]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("ui")
    build_ui.main(out)
    return out


def html(built, name):
    return (built / name).read_text(encoding="utf-8")


def test_all_six_pages_are_rendered(built):
    assert sorted(p.name for p in built.glob("*.html")) == sorted(PAGE_FILES)


def test_the_apps_own_static_files_are_copied_unchanged(built):
    comparison = filecmp.dircmp(Path(BASE_DIR) / "static", built / "static")
    assert not comparison.diff_files and not comparison.left_only and not comparison.right_only
    for sub in comparison.subdirs.values():
        assert not sub.diff_files and not sub.left_only and not sub.right_only


@pytest.mark.parametrize("name", PAGE_FILES)
def test_docker_css_loads_after_every_other_stylesheet(built, name):
    page = html(built, name)
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', page)
    assert sheets[-1] == "docker.css"
    assert sheets.count("docker.css") == 1
    assert 'src="chrome.js"' in page and 'src="shim.js"' in page
    assert page.index('src="shim.js"') < page.index("docker.css")      # the SDK adapter is first


@pytest.mark.parametrize("name", PAGE_FILES)
def test_pages_use_relative_paths_only(built, name):
    page = html(built, name)
    assert not re.findall(r'(?:href|src)="/', page)
    assert "extension-build.invalid" not in page


@pytest.mark.parametrize(
    "name, key, title",
    [
        ("index.html", "overview", "Overview"),
        ("workflows.html", "workflows", "Workflow dependencies"),
        ("deployments.html", "deployments", "Deployment topology"),
        ("failure-impacts.html", "failure-impacts", "Failure impact"),
        ("recommendations.html", "recommendations", "Recommendations"),
        ("connect.html", "connect", "Connect cluster"),
    ],
)
def test_each_page_has_a_key_and_a_readable_sentence_case_title(built, name, key, title):
    page = html(built, name)
    assert f'<body data-page="{key}">' in page
    assert f'<h1 class="topbar__title">{title}</h1>' in page


@pytest.mark.parametrize("name", PAGE_FILES)
def test_navigation_has_six_sentence_case_labels_and_one_active_item(built, name):
    page = html(built, name)
    labels = re.findall(r'<span class="sidebar__link-label">([^<]+)</span>', page)
    assert labels == ["Overview", "Workflow dependencies", "Deployment topology", "Failure impact", "Recommendations", "Connect cluster"]
    assert len(re.findall(r"sidebar__link is-active", page)) == 1


def test_overview_puts_the_cluster_connection_first(built):
    page = html(built, "index.html")
    assert 'data-role="cluster-panel"' in page
    assert page.index('data-role="cluster-panel"') < page.index('class="page-header"') < page.index('class="metric-grid"')
    assert 'data-role="connect-file"' not in page


def test_recommendations_say_what_approval_does_before_asking_for_it(built):
    page = html(built, "recommendations.html")
    gate = page[page.index('id="approval-section"'):]
    assert gate.index("Nothing is applied to your cluster") < gate.index("approval-state-container")
    assert "does NOT apply the change automatically" in page      # the app's own dialog text is untouched


def test_connect_page_has_every_control_the_script_expects(built):
    page = html(built, "connect.html")
    for role in ["connect-status", "connect-title", "connect-summary", "connect-details", "connect-server", "connect-credential",
                 "connect-expiry", "connect-message", "connect-disconnect", "connect-file", "connect-filename", "connect-submit"]:
        assert f'data-role="{role}"' in page, role
    assert 'src="connect.js"' in page and 'data-role="cluster-panel"' not in page
    assert 'role="alert"' in page


# ---- the stylesheet ------------------------------------------------------------------------------

CSS = (UI_SRC / "docker.css").read_text(encoding="utf-8")


def test_stylesheet_braces_balance():
    assert CSS.count("{") == CSS.count("}")


def test_theme_values_are_dockers_own():
    # Read from Docker Desktop 4.95.0's injected theme: primary, paper, secondary text, each in light and dark.
    for pair in ["#1d63ed, #2986ff", "#ffffff, #10151b", "#677285, #7794ab", "#2e7f74, #00a58c", "#d52536, #e65264"]:
        assert pair in CSS


@pytest.mark.parametrize("rule", [
    r"\[hidden\]\s*\{\s*display:\s*none\s*!important",                       # author display rules must not defeat hidden
    r"\.modal-backdrop\s*\{[^}]*display:\s*none",                             # the dialog is hidden until asked for
    r"\.modal-backdrop\.is-visible\s*\{[^}]*display:\s*flex",
    r"\.primary-button\s*\{",                                                 # controls the app uses but never styled
    r"\.danger-button\s*\{",
    r"\.form-input",
])
def test_stylesheet_defines_what_the_app_leaves_unstyled(rule):
    assert re.search(rule, CSS), rule


def test_navigation_labels_wrap_instead_of_truncating():
    assert re.search(r"\.sidebar__link-label\s*\{[^}]*white-space:\s*normal", CSS)
    assert "ellipsis" not in re.search(r"\.sidebar__link-label\s*\{[^}]*\}", CSS).group(0)


def test_no_heavy_decoration():
    assert "box-shadow: none" in CSS and "gradient" not in CSS.split("Workflow dependencies")[0]
