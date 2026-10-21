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
    for compared in [comparison, *comparison.subdirs.values()]:
        assert not compared.diff_files
        assert not compared.left_only
        assert not compared.right_only


@pytest.mark.parametrize("name", PAGE_FILES)
def test_docker_css_loads_after_every_other_stylesheet(built, name):
    page = html(built, name)
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', page)
    assert sheets[-1] == "docker.css"
    assert sheets.count("docker.css") == 1
    assert 'src="chrome.js"' in page
    assert 'src="shim.js"' in page
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
    assert 'src="connect.js"' in page
    assert 'data-role="cluster-panel"' not in page
    assert 'role="alert"' in page


AUTO_ROLES = ["auto-card", "auto-note", "auto-controls", "auto-clusters", "auto-refresh", "auto-connect", "auto-remove",
              "auto-message", "auto-attention", "auto-attention-text", "auto-retry", "manual-details"]


@pytest.mark.parametrize("name", PAGE_FILES)
def test_every_page_runs_autoconnect_after_chrome(built, name):
    page = html(built, name)
    assert 'src="autoconnect.js"' in page, name
    # chrome.js listens for the event autoconnect.js sends, so it must be loaded first.
    assert page.index('src="chrome.js"') < page.index('src="autoconnect.js"')


def test_only_the_workflows_page_loads_the_trace_service_bridge_and_after_autoconnect(built):
    for name in PAGE_FILES:
        page = html(built, name)
        if name == "workflows.html":
            assert 'src="trace-service.js"' in page
            assert page.index('src="autoconnect.js"') < page.index('src="trace-service.js"')
        else:
            assert 'src="trace-service.js"' not in page, name


def test_the_trace_service_bridge_only_ever_asks_the_helper_for_its_three_fixed_commands():
    code = (UI_SRC / "trace-service.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    commands = set(re.findall(r'"(trace-(?:status|start|stop))"', code))
    assert commands == {"trace-status", "trace-start", "trace-stop"}
    # The helper is never given a page-supplied value: exec is called with a file and a one-item array.
    assert code.count("env.exec(") == 1
    assert code.count("env.exec(helperFile(env.platform), [command])") == 1


def test_connect_page_offers_automatic_setup_and_keeps_the_manual_fallback(built):
    page = html(built, "connect.html")
    for role in AUTO_ROLES:
        assert f'data-role="{role}"' in page, role
    assert 'src="connect-auto.js"' in page
    # The manual connection is still there, behind an Advanced disclosure and still complete.
    assert "Advanced: use a kubeconfig file" in page
    assert 'data-role="connect-file"' in page and 'data-role="connect-submit"' in page
    assert page.index('data-role="auto-card"') < page.index('data-role="manual-details"')


def test_the_automatic_card_starts_hidden_until_the_script_decides_what_to_offer(built):
    page = html(built, "connect.html")
    assert re.search(r'<section[^>]*data-role="auto-card"[^>]*\shidden', page)


def test_the_page_says_nothing_happens_without_approval_in_a_system_dialog(built):
    page = html(built, "connect.html")
    assert "A system dialog shows the exact cluster" in page
    assert "nothing is changed unless you approve there" in page


@pytest.mark.parametrize("script", ["autoconnect.js", "connect-auto.js", "trace-service.js"])
def test_the_new_scripts_never_write_html_or_log(script):
    source = (UI_SRC / script).read_text(encoding="utf-8")
    # Comments may discuss these APIs (they explain why they are not used); only code is checked.
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    # Cluster and context names come from the user's kubeconfig; they may only ever be written as text.
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code and "document.write" not in code
    # Nothing here may log: helper output must never reach a console.
    assert not re.search(r"\bconsole\.", code)
    assert "eval(" not in code and "new Function" not in code


def test_automatic_setup_is_limited_to_platforms_that_were_verified():
    code = (UI_SRC / "autoconnect.js").read_text(encoding="utf-8")
    assert 'const ENABLED_PLATFORMS = ["darwin", "win32", "linux"];' in code


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
    assert "box-shadow: none" in CSS
    assert "gradient" not in CSS.split("Workflow dependencies")[0]


def test_build_refuses_a_folder_that_is_not_an_earlier_build(tmp_path):
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "notes.txt").write_text("keep me")
    with pytest.raises(SystemExit):
        build_ui.main(precious)
    assert (precious / "notes.txt").read_text() == "keep me"


@pytest.mark.parametrize("outside", [Path.home(), Path("/")])
def test_build_refuses_paths_outside_the_working_and_temp_directories(outside):
    with pytest.raises(SystemExit):
        build_ui.main(outside)
