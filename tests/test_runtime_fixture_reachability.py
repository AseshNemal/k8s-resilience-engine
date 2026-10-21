"""Gate: no route handler can reach code that reads built-in fixture or sample data.

``tests/test_no_sample_runtime.py`` scans the routing, page and image layers. This test covers the engine
layer behind them: it builds an import-resolved call graph of ``app/`` and ``extension/`` and fails if any
route handler can reach a fixture reader, however deep. A new module that imports a fixture loader also
fails until it is reviewed and added to the allow-list below.

Limits (stated, not hidden): calls on objects (``repo.method()``) and dynamic dispatch are not followed. The
empirical probe at the bottom backs the static analysis by calling every GET route in a clean process.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support import labelled_research_routes as research
from tests.support.reachability import CodeIndex, fixture_sinks, route_entries

ROOT = Path(__file__).resolve().parents[1]

# Modules allowed to import a fixture loader. Each is a research/test fixture consumer or a documented,
# frozen exception. A route must still not reach them (checked below).
ALLOWED_FIXTURE_IMPORTERS = {
    # defines the loaders
    "app.engine.workflow_dependency_modeling.fixture_repository": "Member 1: loads sample_data via sample_loader",
    # Member 1: research/benchmark functions of the public service interface; no kept route calls them
    "app.engine.workflow_dependency_modeling.service": "Member 1: benchmark/evaluation helpers",
    "app.engine.workflow_dependency_modeling.execution_service": "Member 1: run_benchmark_batch (research)",
    "app.engine.workflow_dependency_modeling.evaluator": "Member 1: evaluate_workflow_by_id helper (research)",
    # Member 4: get_sample_recommendation / get_sample_validation, test fixtures only
    "app.engine.recommendation_evaluation_validation.service": "Member 4: test-fixture loaders",
    # Member 3 is frozen; this module still imports fixture_provider. Reported, not changed.
    "app.engine.failure_impact_prediction.service": "Member 3: demo/sample helpers, unreachable from routes",
    "app.engine.failure_impact_prediction.validation_demo": "Member 3: synthetic validation demo, labelled as such",
}

FIXTURE_MODULES = (
    "app.engine.workflow_dependency_modeling.fixture_repository",
    "app.shared.sample_loader",
    "app.engine.failure_impact_prediction.fixture_provider",
)


def write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def entries_reaching_sinks(index: CodeIndex) -> dict[str, list[str]]:
    sinks = fixture_sinks(index)
    return {
        f"{module}.{name}": path
        for (module, name), path in index.reachable(route_entries(index), sinks).items()
    }


# ---- the analysis itself: it must find real patterns and ignore name collisions ------------------------------

@pytest.fixture
def mini_codebase(tmp_path):
    write(tmp_path, "app/shared/sample_loader.py", "def load_sample_json(name):\n    return open(name).read()\n")
    write(tmp_path, "app/engine/fixture_provider.py", "def get_demo_inputs():\n    return 1\n")
    # a service that re-exports a fixture reader (like the public service interfaces do)
    write(tmp_path, "app/engine/a/inner.py", "from app.shared.sample_loader import load_sample_json\n\ndef get_data():\n    return load_sample_json('x')\n")
    write(tmp_path, "app/engine/a/service.py", "from app.engine.a.inner import get_data\n\n__all__ = ['get_data']\n")
    return tmp_path


def test_direct_and_re_exported_calls_are_found(mini_codebase):
    write(mini_codebase, "app/api/routes.py",
          "from app.engine.a.service import get_data\n\n@router.get('/x')\ndef read():\n    return get_data()\n")

    found = entries_reaching_sinks(CodeIndex.build(mini_codebase, ("app",)))

    assert list(found) == ["app.api.routes.read"]
    assert found["app.api.routes.read"][-1].endswith("load_sample_json")


def test_module_qualified_calls_are_found(mini_codebase):
    write(mini_codebase, "app/api/routes.py",
          "from app.engine import fixture_provider\n\n@router.get('/x')\ndef read():\n    return fixture_provider.get_demo_inputs()\n")

    assert list(entries_reaching_sinks(CodeIndex.build(mini_codebase, ("app",)))) == ["app.api.routes.read"]


def test_a_function_passed_as_a_callback_counts_as_reachable(mini_codebase):
    write(mini_codebase, "app/api/routes.py",
          "from app.engine.a.service import get_data\n\n@router.get('/x')\ndef read():\n    return run(get_data)\n")

    assert list(entries_reaching_sinks(CodeIndex.build(mini_codebase, ("app",)))) == ["app.api.routes.read"]


def test_a_same_named_method_on_another_object_is_not_a_false_positive(mini_codebase):
    write(mini_codebase, "app/engine/repo.py", "class Repo:\n    def get_data(self):\n        return 1\n")
    write(mini_codebase, "app/api/routes.py",
          "from app.engine.repo import Repo\n\n@router.get('/x')\ndef read():\n    return Repo().get_data()\n")

    assert entries_reaching_sinks(CodeIndex.build(mini_codebase, ("app",))) == {}


def test_a_route_that_never_touches_fixtures_is_clean(mini_codebase):
    write(mini_codebase, "app/api/routes.py", "@router.get('/x')\ndef read():\n    return 1\n")

    assert entries_reaching_sinks(CodeIndex.build(mini_codebase, ("app",))) == {}


def test_a_locally_imported_fixture_reader_is_found(mini_codebase):
    write(mini_codebase, "app/api/routes.py",
          "@router.get('/x')\ndef read():\n    from app.engine.a.service import get_data\n    return get_data()\n")

    assert list(entries_reaching_sinks(CodeIndex.build(mini_codebase, ("app",)))) == ["app.api.routes.read"]


# ---- the real code base -----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_index():
    return CodeIndex.build(ROOT, ("app", "extension"))


def test_the_analysis_actually_sees_the_application(real_index):
    assert len(route_entries(real_index)) > 60
    assert len(fixture_sinks(real_index)) > 10


def test_no_route_handler_can_reach_a_fixture_reader(real_index):
    assert entries_reaching_sinks(real_index) == {}


def test_only_reviewed_modules_import_fixture_loaders(real_index):
    importers = {
        module for module in real_index.modules
        if module not in FIXTURE_MODULES and real_index.imports_any(module, FIXTURE_MODULES)
    }

    unreviewed = sorted(importers - set(ALLOWED_FIXTURE_IMPORTERS))
    assert unreviewed == [], f"new fixture importers need review: {unreviewed}"


def test_the_graph_evaluation_service_no_longer_imports_a_fixture_loader(real_index):
    assert not real_index.imports_any(
        "app.engine.workflow_dependency_modeling.dependency_assessment_service", FIXTURE_MODULES
    )


# ---- empirical probe in a clean process ---------------------------------------------------------------------------

RESEARCH_PATHS = research.PATHS | research.DECLARED_STATE_PATHS

PROBE = r"""
import json, logging, os, re, sys
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.getcwd())
opened = []
def hook(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0])
        if "sample_data" in path or "deploy/test-workloads" in path:
            opened.append(path)
sys.addaudithook(hook)

from alembic import command
from alembic.config import Config
config = Config("alembic.ini"); config.set_main_option("script_location", "alembic"); command.upgrade(config, "head")
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app, raise_server_exceptions=False)
statuses, hits = {}, []
for path, operations in client.get("/openapi.json").json()["paths"].items():
    if "get" not in operations:
        continue
    url = re.sub(r"\{[^}]+\}", "x", path)
    params = {p["name"]: "x" for p in operations["get"].get("parameters", []) if p["in"] == "query" and p.get("required")}
    before = len(opened)
    code = client.get(url, params=params).status_code
    statuses[code] = statuses.get(code, 0) + 1
    if len(opened) > before:
        hits.append([path, opened[before:]])
print(json.dumps({"routes": sum(statuses.values()), "statuses": statuses, "fixture_opens": hits}))
"""


def test_calling_every_get_route_never_opens_a_fixture_file(tmp_path):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "DATABASE_URL": f"sqlite:///{tmp_path / 'probe.db'}",
        "KUBECONFIG": str(tmp_path / "no-kubeconfig"),
        # Windows needs these even in a hand-built environment, or the network stack (asyncio) cannot start.
        **{k: os.environ[k] for k in ("SYSTEMROOT", "SystemDrive") if k in os.environ},
    }

    completed = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)

    assert completed.returncode == 0, completed.stderr[-2000:]
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["routes"] > 40
    # Only the labelled research routes may open a fixture; any other route doing so is a regression.
    unlabelled = [hit for hit in report["fixture_opens"] if hit[0] not in RESEARCH_PATHS]
    assert unlabelled == []
    assert "500" not in report["statuses"]  # every failure is a coded evidence error, not a crash
