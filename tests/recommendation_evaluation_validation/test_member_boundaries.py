"""Guard unrelated component files while allowing the explicitly requested integration work.

The branch under test is compared with where it left ``main`` (the merge base), plus whatever is changed or untracked in the working
tree, so the guard holds before and after a commit. Component 3's evaluator and Component 2's discovery are CALLED by Member 4
(``application/live_analyser.py``). The 2026-11-16 recovery request authorizes changes
across components for deployment-only prediction and hybrid workflow management.
It reads the repository's git state only.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Other members' engine components, their API routers, pages (template, script, style) and the contracts everyone shares.
PROTECTED = (
    "app/engine/workflow_dependency_modeling/",
    "app/engine/deployment_topology_modeling/",
    "app/engine/failure_impact_prediction/",
    "app/api/workflows.py", "app/api/deployments.py", "app/api/failure_impacts.py",
    "app/templates/components/workflows.html", "app/templates/components/deployments", "app/templates/components/failure_impacts.html",
    "app/static/js/components/workflows.js", "app/static/js/components/deployments", "app/static/js/components/failure_impacts.js",
    "app/static/js/workflow-discovery-poller.js",
    "app/static/css/pages/workflows.css", "app/static/css/pages/deployments", "app/static/css/pages/failure_impacts.css",
    "app/shared/schemas/",
    "app/main.py",
)

# Exact integration files authorized by the recovery/hybrid-dashboard request;
# unrelated files remain guarded. Keep the service-interface assertion below.
AUTHORIZED_INTEGRATION_PATHS = {
    # The production workflow evidence policy explicitly covers Member 1 provenance and Member 3 runtime inputs.
    "app/engine/workflow_dependency_modeling/operational_evidence.py",
    "app/engine/failure_impact_prediction/runtime_provider.py",
    # Production-readiness request explicitly authorizes shared probe transport hardening across components.
    "app/engine/workflow_dependency_modeling/workflow_probe_runner.py",
    "app/api/failure_impacts.py", "app/api/workflows.py",
    "app/engine/failure_impact_prediction/deployment_impact.py",
    "app/engine/failure_impact_prediction/service.py",
    # Member 3's own whole-cluster overview (the Failure impact page's single read) and its page styles.
    "app/engine/failure_impact_prediction/overview.py", "app/engine/failure_impact_prediction/workflow_basis.py",
    "app/static/css/pages/failure_impacts.css",
    # The probe entry point is a Service that selects the application's pods; discovery must leave it out of the topology (two one-line filters).
    "app/engine/deployment_topology_modeling/discovery.py", "app/engine/deployment_topology_modeling/live_state/ownership_resolver.py",
    "app/engine/workflow_dependency_modeling/management.py",
    "app/engine/workflow_dependency_modeling/service.py",
    # New files of the same authorized hybrid-workflow work: service.py (above) imports test_discovery, which imports the
    # evidence schema. They are named exactly; nothing else under either directory is opened.
    "app/engine/workflow_dependency_modeling/test_discovery.py",
    "app/shared/schemas/workflow_test_evidence.py",
    "app/shared/schemas/failure_impact.py", "app/shared/schemas/workflow_management.py",
    "app/static/js/components/failure_impacts.js",
    "app/templates/components/failure_impacts.html", "app/templates/components/workflows.html",
}


def git(*arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def changed_paths() -> set[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    try:
        base = git("merge-base", "main", "HEAD").strip()
    except subprocess.CalledProcessError:
        pytest.skip("no main branch to compare with")
    committed = set(git("diff", "--name-only", base, "HEAD").split())
    working = set(git("diff", "--name-only", "HEAD").split())
    untracked = set(git("ls-files", "--others", "--exclude-standard").split())
    return committed | working | untracked


def test_no_unrelated_component_page_or_shared_contract_is_changed():
    touched = sorted(path for path in changed_paths()
                     if path.startswith(PROTECTED) and path not in AUTHORIZED_INTEGRATION_PATHS)

    assert touched == [], f"another member's files were changed: {touched}"


def test_member_four_reaches_the_other_components_only_through_their_public_services():
    analyser = (ROOT / "app/engine/recommendation_evaluation_validation/application/live_analyser.py").read_text()

    assert "from app.engine.failure_impact_prediction.service import predict_failure_impact" in analyser
    assert "from app.engine.failure_impact_prediction.runtime_provider import get_runtime_component_inputs" in analyser
    assert "from app.engine.deployment_topology_modeling.service import discover_live" in analyser
    assert "blast_radius =" not in analyser and "calculate_blast_radius" not in analyser, "no impact algorithm is re-implemented here"
