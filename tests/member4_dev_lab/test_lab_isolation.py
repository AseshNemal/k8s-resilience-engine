"""Isolation guarantees of the Member 4 Development Lab.

The lab is a research tool: it must stay out of the production app and the Docker Desktop extension,
must never touch Kubernetes, and must never let offline evidence pose as live evidence.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = "experiments.member4_dev_lab"


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add(node.module or "")
    return names


def test_the_product_app_does_not_mount_the_lab():
    from app.main import app

    paths = [getattr(route, "path", "") for route in app.routes]
    assert not any("dev-lab" in path for path in paths)


def test_nothing_in_the_product_imports_the_lab():
    offenders = []
    for folder in ("app", "extension"):
        for path in (ROOT / folder).rglob("*.py"):
            if any(LAB == name or name.startswith(f"{LAB}.") for name in _imports(path)):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_importing_the_product_app_does_not_import_the_lab():
    """Checked in a clean process: in this pytest session the lab is legitimately imported already."""
    import subprocess
    import sys

    code = (
        "import sys, app.main; "
        "print(any(m == 'experiments.member4_dev_lab' or m.startswith('experiments.member4_dev_lab.') "
        "for m in sys.modules))"
    )
    completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr[-1000:]
    assert completed.stdout.strip() == "False"


def test_the_extension_ui_does_not_build_a_lab_page():
    source = (ROOT / "extension" / "build_ui.py").read_text(encoding="utf-8")
    assert "dev-lab" not in source
    assert "dev_lab" not in source


def test_the_lab_never_imports_kubernetes_access():
    for path in (ROOT / "experiments" / "member4_dev_lab").rglob("*.py"):
        for name in _imports(path):
            root = name.split(".")[0]
            assert root not in ("kubernetes", "urllib3"), f"{path.name} imports {name}"
            assert not name.startswith("app.integrations.kubernetes"), f"{path.name} imports {name}"


def test_the_lab_serves_only_offline_provenance():
    """Every evaluation the lab serves carries the offline source, never the live one."""
    from fastapi.testclient import TestClient

    from experiments.member4_dev_lab.app import create_app
    from tests.support.member4_harness import OFFLINE_SOURCE

    client = TestClient(create_app())
    for scenario in client.get("/api/dev-lab/scenarios").json()["scenarios"]:
        detail = client.get(f"/api/dev-lab/scenarios/{scenario['name']}").json()
        assert detail["provenance"]["source"] == OFFLINE_SOURCE
        assert detail["provenance"]["isLiveEvidence"] is False
        assert detail["detail"]["evidence"]["isPrediction"] is True


def test_a_caller_cannot_promote_offline_evidence_through_the_lab():
    """The lab has no approval surface at all: no route accepts provenance, evidence or decisions."""
    from fastapi.testclient import TestClient

    from experiments.member4_dev_lab.app import create_app
    from tests.support.member4_harness import OFFLINE_SOURCE

    client = TestClient(create_app())
    for method in (client.post, client.put, client.patch, client.delete):
        assert method("/api/dev-lab/scenarios").status_code == 405
        assert method("/api/dev-lab/scenarios/concentrated-replicas").status_code == 405
        assert method("/api/dev-lab/scenarios/concentrated-replicas/approve").status_code in (404, 405)
    # a forged provenance query parameter is ignored, not honoured
    detail = client.get(
        "/api/dev-lab/scenarios/concentrated-replicas",
        params={"source": "live-discovery", "evidence_source": "live-discovery"},
    ).json()
    assert detail["provenance"]["source"] == OFFLINE_SOURCE
    assert detail["detail"]["yaml"]["applicable"] is False
