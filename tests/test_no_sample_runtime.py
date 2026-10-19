"""Gate: nothing that runs in the product may load, serve or display sample, demo or fixture data.

Fixture loaders still exist for automated tests and research experiments (``get_sample_*`` in the engine
services, ``sample_data/``, ``experiments/``). This test fails if a *runtime* module, template, page
script or image recipe starts depending on them again.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Modules on the live request path: routers, pages, the app entry points, discovery and the
# provider/approval code that must be bound to live evidence.
RUNTIME_PYTHON = [
    *sorted((ROOT / "app" / "api").glob("*.py")),
    *sorted((ROOT / "app" / "web").glob("*.py")),
    ROOT / "app" / "main.py",
    ROOT / "app" / "engine" / "deployment_topology_modeling" / "discovery.py",
    ROOT / "app" / "engine" / "failure_impact_prediction" / "runtime_provider.py",
    ROOT / "app" / "integrations" / "kubernetes" / "client.py",
    *sorted((ROOT / "extension").glob("*.py")),
]

PYTHON_FORBIDDEN = re.compile(
    r"sample_loader|fixture_provider|sample_data|get_sample_|get_demo_|predict_demo_|seed_demo_traces"
    r"|boutique\.yaml|deploy/test-workloads|load_kube_config\("
)

FRONTEND_FILES = [
    *sorted((ROOT / "app" / "templates").rglob("*.html")),
    *sorted((ROOT / "app" / "static" / "js").rglob("*.js")),
]
FRONTEND_FORBIDDEN = re.compile(
    r"/sample\b|/demo-deployment|/failure-impacts/simulate|benchmarks/ensemble|chaos/benchmarks|jaeger/seed"
    r"|api/workflows/benchmark|evaluation/accuracy|Sample Data|Reload Sample|Load Synthetic Fixture"
    r"|Seed Synthetic|Contract Fixtures|Live Cluster \(KinD\)|Sample Virtual Placement|sample demonstration",
    re.IGNORECASE,
)

PYTHON_NAMES = [p.relative_to(ROOT).as_posix() for p in RUNTIME_PYTHON]
FRONTEND_NAMES = [p.relative_to(ROOT).as_posix() for p in FRONTEND_FILES]


def offenses(text: str, pattern: re.Pattern) -> list[str]:
    return sorted({m.group(0) for m in pattern.finditer(text)})


def code_only(source: str) -> str:
    """Drop comment lines and docstring-style prose so explanations may mention what was removed."""
    lines = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
    return re.sub(r'""".*?"""', "", "\n".join(lines), flags=re.DOTALL)


@pytest.mark.parametrize("path", RUNTIME_PYTHON, ids=PYTHON_NAMES)
def test_runtime_python_does_not_touch_fixtures_or_ambient_kubeconfig(path):
    assert offenses(code_only(path.read_text(encoding="utf-8")), PYTHON_FORBIDDEN) == []


@pytest.mark.parametrize("path", FRONTEND_FILES, ids=FRONTEND_NAMES)
def test_pages_and_scripts_do_not_reference_sample_endpoints_or_labels(path):
    assert offenses(path.read_text(encoding="utf-8"), FRONTEND_FORBIDDEN) == []


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "extension/Dockerfile"])
def test_images_do_not_ship_fixture_or_test_workload_folders(dockerfile):
    copied = [
        line for line in (ROOT / dockerfile).read_text(encoding="utf-8").splitlines()
        if line.startswith("COPY") and re.search(r"\bsample_data\b|\bdeploy\b", line)
    ]
    assert copied == []


def test_the_scanner_would_catch_what_it_guards_against():
    """Self-test: the patterns match the exact constructs this change removed."""
    assert offenses("from app.shared.sample_loader import load_sample_json", PYTHON_FORBIDDEN)
    assert offenses("return get_sample_deployment()", PYTHON_FORBIDDEN)
    assert offenses("config.load_kube_config()", PYTHON_FORBIDDEN)
    assert offenses('default_manifest = root / "deploy" / "kubernetes" / "boutique.yaml"', PYTHON_FORBIDDEN)
    assert offenses('fetchJson("/api/deployments/sample")', FRONTEND_FORBIDDEN)
    assert offenses('badge.textContent = "Live Cluster (KinD)";', FRONTEND_FORBIDDEN)
    assert offenses("<span>Sample Data</span>", FRONTEND_FORBIDDEN)
    assert offenses("There is no sample data to display yet.", FRONTEND_FORBIDDEN)
    assert offenses("/api/workflows/jaeger/seed", FRONTEND_FORBIDDEN)
    assert not offenses("const live = await fetchJson('/api/deployments/discovery');", FRONTEND_FORBIDDEN)


def test_the_research_routes_live_only_under_tests():
    # They are importable for the research tests, but app.main must never include them.
    from app.main import app

    paths = app.openapi()["paths"]
    assert not [p for p in paths if "sample" in p or "benchmark" in p or "chaos/run" in p]
