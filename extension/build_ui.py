#!/usr/bin/env python3
"""Build-time only: render the existing dashboard pages to static HTML for the extension UI.

Runs the unmodified FastAPI app once with a test client, saves each page, and post-processes the
HTML for Docker Desktop: asset and navigation paths become relative, docker.css (the Docker look)
and chrome.js are added, shim.js is loaded first so the pages' fetch() calls reach the backend
through the Docker Desktop SDK, and a "Connect cluster" page is added. No research page, template,
script or style under app/ is edited.

Usage: build_ui.py OUTPUT_DIR
"""

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import BASE_DIR, app

# url_for() renders absolute URLs against the test client's origin; it is stripped below.
BUILD_ORIGIN = "https://extension-build.invalid"
INDEX_PAGE = "index.html"

# route -> (file, page key used by the CSS, header title in Docker's sentence case)
PAGES = {
    "/": (INDEX_PAGE, "overview", "Overview"),
    "/components/workflows": ("workflows.html", "workflows", "Workflow dependencies"),
    "/components/deployments": ("deployments.html", "deployments", "Deployment topology"),
    "/components/failure-impacts": ("failure-impacts.html", "failure-impacts", "Failure impact"),
    "/components/recommendations": ("recommendations.html", "recommendations", "Recommendations"),
}
# The app's navigation labels are Title Case; Docker Desktop uses sentence case.
NAV_LABELS = {
    "Workflow Dependencies": "Workflow dependencies",
    "Deployment Topology": "Deployment topology",
    "Failure Impact": "Failure impact",
}

CONNECT_ICON = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
    '<path d="M9 7V3m6 4V3M7 7h10v4a5 5 0 0 1-10 0V7zm5 9v5" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
CONNECT_LINK = f"""<a
            class="sidebar__link" data-role="connect-link"
            href="connect.html"
        >
            <span class="sidebar__link-icon">{CONNECT_ICON}</span>
            <span class="sidebar__link-label">Connect cluster</span>
        </a>

        """

# Overview: connection status first. chrome.js fills it from GET /_extension/cluster.
CLUSTER_PANEL = """<section class="cluster-panel" data-role="cluster-panel" data-state="none" aria-live="polite">
    <div class="cluster-panel__text">
        <div class="cluster-panel__title" data-role="cluster-panel-title">Checking cluster connection…</div>
        <div class="cluster-panel__detail" data-role="cluster-panel-detail"></div>
    </div>
    <a class="secondary-button" data-role="cluster-panel-link" href="connect.html" hidden></a>
</section>
"""

CONNECT_MAIN = """<main class="page" id="main-content">
<div class="connect">
    <section class="card" aria-labelledby="connect-title">
        <div class="connect__status" data-role="connect-status" data-state="none">
            <span class="connect__dot" aria-hidden="true"></span>
            <div>
                <h2 class="connect__title" id="connect-title" data-role="connect-title">Checking…</h2>
                <p class="connect__summary" data-role="connect-summary"></p>
            </div>
            <button type="button" class="secondary-button" data-role="connect-disconnect" hidden>Disconnect</button>
        </div>
        <dl class="connect__details" data-role="connect-details" hidden>
            <dt>Cluster API</dt><dd data-role="connect-server"></dd>
            <dt>Credential</dt><dd data-role="connect-credential"></dd>
            <dt>Access</dt><dd>Approved for full access (read and write). Reads were checked when you connected; writes are checked when a change is applied.</dd>
            <dt>Renewal</dt><dd data-role="connect-expiry"></dd>
        </dl>
    </section>

    <section class="card" aria-labelledby="connect-auto-title" data-role="auto-card" hidden>
        <h3 class="section-header__title" id="connect-auto-title">Set up automatically</h3>
        <p class="ui-note" data-role="auto-note"></p>
        <div data-role="auto-controls">
            <p class="connect__lead">Local clusters (kind, or Docker Desktop's Kubernetes) appear here by themselves, marked
                <em>Not connected</em> until you connect one. Connect it and approve full access once: the
                extension then keeps the credential renewed, reconnects the cluster by itself if the connection is
                lost, and reads the applications in it. A cluster you disconnect stays disconnected until you connect
                it again.</p>
            <ul class="connect__clusters" data-role="auto-clusters" aria-live="polite"></ul>
            <div class="connect__picker">
                <button type="button" class="secondary-button" data-role="auto-refresh">Find clusters</button>
                <button type="button" class="primary-button" data-role="auto-connect" disabled>Connect</button>
                <button type="button" class="secondary-button" data-role="auto-remove" hidden>Remove access…</button>
            </div>
            <p class="connect__message" data-role="auto-message" role="status"></p>
            <p class="connect__attention" data-role="auto-attention" role="alert" hidden>
                <span data-role="auto-attention-text"></span>
                <button type="button" class="secondary-button" data-role="auto-retry">Try again</button>
            </p>
            <p class="ui-note">
                A system dialog shows the exact cluster, the permissions and every change before anything happens, and
                nothing is changed unless you approve there. Your own kubeconfig stays on this computer; the extension
                only ever receives a short-lived token.
            </p>
        </div>
    </section>

    <section class="card" aria-labelledby="connect-apps-title" data-role="apps-card" hidden>
        <h3 class="section-header__title" id="connect-apps-title">Applications in your cluster</h3>
        <p class="ui-note" data-role="apps-status" role="status" aria-live="polite"></p>
        <ul class="connect__apps" data-role="apps-list"></ul>
        <p class="ui-note">
            Read from the connected cluster by the Deployment topology discovery and refreshed while this page is
            visible. Namespaces and services appear only as the cluster reports them: nothing is added, and
            namespaces whose placement could not be fully resolved are marked incomplete. Pick a namespace on the
            Failure impact and Recommendations pages from this list.
        </p>
    </section>

    <details class="card connect__manual" data-role="manual-details" open>
        <summary class="section-header__title" id="connect-new-title">Advanced: use a kubeconfig file</summary>
        <ol class="connect__steps">
            <li>In the project folder, run <code>./scripts/extension-cluster-access.sh</code> (on Windows, <code>scripts\\extension-cluster-access.cmd</code>). It asks before
                creating a full-access service account in your kind cluster and writes
                <code>.kube/extension-readonly.kubeconfig</code>.</li>
            <li>Choose that file here. The app checks that the credential can read the cluster and
                refuses it otherwise.</li>
        </ol>
        <div class="connect__picker">
            <input type="file" id="connect-file" class="connect__file" data-role="connect-file">
            <label for="connect-file" class="secondary-button">Choose kubeconfig…</label>
            <span class="connect__filename" data-role="connect-filename"></span>
            <button type="button" class="primary-button" data-role="connect-submit" disabled>Connect</button>
        </div>
        <p class="connect__message" data-role="connect-message" role="alert"></p>
        <p class="ui-note">
            The file goes only to the extension's local backend and is stored in its private data volume.
            Its contents are never shown or logged. A credential connected this way is not renewed
            automatically: run the script again to renew, or
            <code>./scripts/extension-cluster-access.sh --revoke</code> to remove access immediately.
        </p>
    </details>
</div>
</main>"""


def _static_paths(html: str, key: str, title: str) -> str:
    for route, (name, _key, _title) in PAGES.items():
        html = html.replace(f'href="{route}"', f'href="{name}"')
    html = re.sub(rf'((?:href|src)=")(?:{re.escape(BUILD_ORIGIN)})?/static/', r"\1static/", html)
    html = html.replace("<head>", '<head>\n    <script src="shim.js"></script>', 1)
    # The Docker look loads after every app stylesheet (including the page-specific ones) so it wins.
    html = html.replace("</head>", '    <link rel="stylesheet" href="docker.css">\n</head>', 1)
    html = html.replace("<body>", f'<body data-page="{key}">', 1)
    html = html.replace("</body>", '<script src="chrome.js" defer></script>\n<script src="autoconnect.js" defer></script>\n<script src="namespaces.js" defer></script>\n</body>', 1)
    if key == "workflows":
        # Only this page can start the trace service (through the host helper), so only it loads the bridge.
        html = html.replace('<script src="namespaces.js" defer></script>\n</body>',
                            '<script src="namespaces.js" defer></script>\n<script src="trace-service.js" defer></script>\n</body>', 1)

    for old, new in NAV_LABELS.items():
        html = html.replace(f'<span class="sidebar__link-label">{old}</span>', f'<span class="sidebar__link-label">{new}</span>')
    html, count = re.subn(r'(<h1 class="topbar__title">).*?(</h1>)', rf"\g<1>{title}\g<2>", html, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{key}: could not find the header title.")

    html, count = re.subn(
        r'<button\s+type="button"\s+class="sidebar__collapse-btn"',
        lambda m: CONNECT_LINK + m.group(0),
        html,
        count=1,
    )
    if count != 1:
        raise SystemExit("Could not find the sidebar collapse button to add the Connect link.")

    if key == "overview":
        html, count = re.subn(r'<div class="page-header">', lambda _m: CLUSTER_PANEL + '<div class="page-header">', html, count=1)
        if count != 1:
            raise SystemExit("Could not find the Overview page header.")
    if key == "failure-impacts":
        # The synthetic-portfolio demo is a labelled sample-data page of the web app. The extension shows the connected cluster's
        # real evidence and has no such page, so the demo's entry card (and its absolute link) is not part of the extension.
        html = re.sub(r'\s*<section[^>]*data-role="synthetic-portfolio-link".*?</section>', "", html, count=1, flags=re.S)
    if key == "recommendations":
        marker = '<div class="card" id="approval-section">'
        if marker not in html:
            raise SystemExit("Could not find the approval section.")
        note = ('<p class="approval-note">Apply changes the selected Deployments, then starts rollout checks, workflow tests, '
                'resource measurements and risk analysis. Reset restores the saved configuration.</p>')
        html = html.replace(marker, marker + "\n        " + note, 1)
    return html


def _drop_tags(html: str, tag_pattern: str, needle: str) -> str:
    """Remove every tag matching tag_pattern that contains needle, plus the whitespace before it."""
    parts, pos = [], 0
    for match in re.finditer(tag_pattern, html):
        if needle in match.group(0):
            parts.append(html[pos:match.start()].rstrip())
            pos = match.end()
    parts.append(html[pos:])
    return "".join(parts)


def _connect_page(index_html: str) -> str:
    html = _drop_tags(index_html, r"<link[^>]*>", "dashboard.css")
    html = _drop_tags(html, r"<link[^>]*>", "pages/recommendations.css")
    for script in ("dashboard.js", "overview-summary.js", "recommendation-topology.js", "recommendation-apply-test.js"):
        html = _drop_tags(html, r"<script[^>]*></script>", script)
    html = re.sub(r"sidebar__link\s+is-active", "sidebar__link ", html)
    html = html.replace('class="sidebar__link" data-role="connect-link"', 'class="sidebar__link is-active" data-role="connect-link"')
    html = html.replace('<body data-page="overview">', '<body data-page="connect">', 1)
    html = re.sub(r"<title>.*?</title>", "<title>Connect cluster · K8s Resilience Engine</title>", html, count=1, flags=re.S)
    html = html.replace('<h1 class="topbar__title">Overview</h1>', '<h1 class="topbar__title">Connect cluster</h1>')
    html, count = re.subn(r'<main class="page" id="main-content">.*?</main>', lambda _m: CONNECT_MAIN, html, count=1, flags=re.S)
    if count != 1:
        raise SystemExit("Could not find the main content area for the Connect page.")
    return html.replace("</body>", '<script src="connect.js" defer></script>\n<script src="connect-auto.js" defer></script>\n</body>', 1)


def _check_relative(name: str, html: str) -> None:
    leftovers = re.findall(r'(?:href|src)="/[^"]*"', html)
    leftovers += re.findall(rf"{re.escape(BUILD_ORIGIN)}[^\"' )]*", html)
    if leftovers:
        raise SystemExit(f"{name}: absolute paths would break inside the extension: {leftovers[:5]}")


def _checked_output_dir(requested: Path) -> Path:
    """Resolve the output folder and refuse anything but a build folder, since it is emptied first.

    Allowed: the image build's /out, or a folder inside the working directory or the temp directory.
    An existing folder must be empty or an earlier build of this script (it has an index.html).
    """
    output = Path(os.path.realpath(requested))
    inside = [Path(os.path.realpath(base)) for base in (Path.cwd(), tempfile.gettempdir())]
    if output != Path("/out") and not any(base in output.parents for base in inside):
        raise SystemExit(f"Refusing to build into {output}: use /out or a folder inside the working or temp directory.")
    if output.exists() and (not output.is_dir() or (any(output.iterdir()) and not (output / INDEX_PAGE).is_file())):
        raise SystemExit(f"Refusing to replace {output}: it is not empty and is not an earlier build of this script.")
    return output


def main(requested: Path) -> None:
    output = _checked_output_dir(requested)
    client = TestClient(app, base_url=BUILD_ORIGIN)
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(Path(BASE_DIR) / "static", output / "static")

    rendered = {}
    for route, (name, key, title) in PAGES.items():
        response = client.get(route)
        if response.status_code != 200:
            raise SystemExit(f"{route} returned HTTP {response.status_code}")
        rendered[name] = _static_paths(response.text, key, title)

    rendered["connect.html"] = _connect_page(rendered[INDEX_PAGE])
    for name, html in rendered.items():
        _check_relative(name, html)
        (output / name).write_text(html, encoding="utf-8")
    print(f"Rendered {len(rendered)} pages to {output}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
