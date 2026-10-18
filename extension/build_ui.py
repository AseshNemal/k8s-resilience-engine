#!/usr/bin/env python3
"""Build-time only: render the existing dashboard pages to static HTML for the extension UI.

Runs the unmodified FastAPI app once with a test client, saves each page, and rewrites only the
asset and navigation paths so the pages work from static files. It adds a "Connect cluster"
page and sidebar link, and loads shim.js first so the pages' fetch() calls reach the backend
through the Docker Desktop SDK. No research page, template, script or style is edited.

Usage: build_ui.py OUTPUT_DIR
"""

import re
import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import BASE_DIR, app

# url_for() renders absolute URLs against the test client's origin; it is stripped below.
BUILD_ORIGIN = "http://extension-build.invalid"

PAGES = {
    "/": "index.html",
    "/components/workflows": "workflows.html",
    "/components/deployments": "deployments.html",
    "/components/failure-impacts": "failure-impacts.html",
    "/components/recommendations": "recommendations.html",
}

CONNECT_ICON = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
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

CONNECT_MAIN = """<main class="page" id="main-content">
<div class="page-header">
    <h2 class="page-header__title">Connect cluster</h2>
    <p class="page-header__subtitle">
        Give the app read-only access to your local Kubernetes test cluster. The app only reads nodes,
        pods and deployments; it cannot change the cluster.
    </p>
</div>

<section class="card stack-md" aria-labelledby="connect-status-title">
    <h3 class="section-header__title" id="connect-status-title">Connection</h3>
    <p><span class="status-badge status-badge--muted" data-role="connect-badge">Checking…</span></p>
    <dl class="stack-sm" data-role="connect-details" hidden>
        <div><dt class="muted">Cluster API</dt><dd data-role="connect-server"></dd></div>
        <div><dt class="muted">Credential</dt><dd data-role="connect-credential"></dd></div>
        <div><dt class="muted">Expires</dt><dd data-role="connect-expiry"></dd></div>
    </dl>
    <p class="ui-note" data-role="connect-message" role="status"></p>
    <div>
        <button type="button" class="secondary-button" data-role="connect-disconnect" hidden>Disconnect cluster</button>
    </div>
</section>

<section class="card stack-md" aria-labelledby="connect-new-title">
    <h3 class="section-header__title" id="connect-new-title">Connect or renew</h3>
    <ol class="stack-sm">
        <li>In the project folder, run <code>./scripts/extension-cluster-access.sh</code>. It asks before
            creating a read-only service account in the kind cluster and writes
            <code>.kube/extension-readonly.kubeconfig</code>.</li>
        <li>Choose that file here. The app checks that the credential cannot change the cluster and
            refuses it otherwise.</li>
    </ol>
    <p>
        <input type="file" id="connect-file" data-role="connect-file" aria-label="Kubeconfig file">
        <button type="button" class="secondary-button" data-role="connect-submit" disabled>Connect</button>
    </p>
    <p class="ui-note">
        The file is sent only to the extension's local backend and stored in its private data volume.
        Its contents are never shown or logged. Tokens expire; run the script again to renew, or
        <code>./scripts/extension-cluster-access.sh --revoke</code> to remove access immediately.
    </p>
</section>
</main>"""


def _static_paths(html: str) -> str:
    for route, name in PAGES.items():
        html = html.replace(f'href="{route}"', f'href="{name}"')
    html = re.sub(rf'((?:href|src)=")(?:{re.escape(BUILD_ORIGIN)})?/static/', r"\1static/", html)
    html = html.replace("<head>", '<head>\n    <script src="shim.js"></script>', 1)
    html, count = re.subn(
        r'(\s*)(<button\s+type="button"\s+class="sidebar__collapse-btn")',
        lambda m: m.group(1) + CONNECT_LINK + m.group(2),
        html,
        count=1,
    )
    if count != 1:
        raise SystemExit("Could not find the sidebar collapse button to add the Connect link.")
    return html


def _connect_page(index_html: str) -> str:
    html = re.sub(r"\s*<link[^>]*dashboard\.css[^>]*>", "", index_html)
    html = re.sub(r"\s*<script[^>]*dashboard\.js[^>]*></script>", "", html)
    html = re.sub(r"sidebar__link\s+is-active", "sidebar__link ", html)
    html = html.replace('class="sidebar__link" data-role="connect-link"', 'class="sidebar__link is-active" data-role="connect-link"')
    html = re.sub(r"<title>.*?</title>", "<title>Connect cluster · K8s Resilience Engine</title>", html, count=1, flags=re.S)
    html = html.replace('<h1 class="topbar__title">Overview</h1>', '<h1 class="topbar__title">Connect cluster</h1>')
    html, count = re.subn(r'<main class="page" id="main-content">.*?</main>', lambda _m: CONNECT_MAIN, html, count=1, flags=re.S)
    if count != 1:
        raise SystemExit("Could not find the main content area for the Connect page.")
    return html.replace("</body>", '<script src="connect.js" defer></script>\n</body>', 1)


def _check_relative(name: str, html: str) -> None:
    leftovers = re.findall(r'(?:href|src)="/[^"]*"', html)
    leftovers += re.findall(rf"{re.escape(BUILD_ORIGIN)}[^\"' )]*", html)
    if leftovers:
        raise SystemExit(f"{name}: absolute paths would break inside the extension: {leftovers[:5]}")


def main(output: Path) -> None:
    client = TestClient(app, base_url=BUILD_ORIGIN)
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(Path(BASE_DIR) / "static", output / "static")

    rendered = {}
    for route, name in PAGES.items():
        response = client.get(route)
        if response.status_code != 200:
            raise SystemExit(f"{route} returned HTTP {response.status_code}")
        rendered[name] = _static_paths(response.text)

    rendered["connect.html"] = _connect_page(rendered["index.html"])
    for name, html in rendered.items():
        _check_relative(name, html)
        (output / name).write_text(html, encoding="utf-8")
    print(f"Rendered {len(rendered)} pages to {output}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
