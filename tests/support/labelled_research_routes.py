"""The member-owned research routes that stay mounted, and exactly what they may contain.

Component 2 keeps its chaos scenario runner and academic benchmark routes, which run scripted dry-run
scenarios instead of the connected cluster. The sample deployment and the Component 3 contract-fixture
simulations were removed from the product; only the dry-run routes below remain, for the viva
demonstrations, with two conditions:

* each one is named here, so a *new* sample, demo or benchmark route still fails the guards;
* each one labels itself as sample, fixture, dry-run or benchmark data in its OpenAPI summary or in the
  page text, so it can never be mistaken for evidence about the connected cluster.

Everything else must stay real-data-only (tests/test_no_sample_runtime.py,
tests/test_live_evidence_api.py).
"""

# (method, path) of the routes that serve fixture or dry-run data.
ROUTES = {
    ("GET", "/api/deployments/chaos/scenarios"),
    ("GET", "/api/deployments/chaos/scenarios/{scenario_id}"),
    ("POST", "/api/deployments/chaos/run/{scenario_id}"),
    ("GET", "/api/deployments/chaos/matrix/{scenario_id}"),
    ("GET", "/api/deployments/chaos/benchmarks"),
    ("GET", "/api/deployments/benchmarks/accuracy"),
    ("GET", "/api/deployments/benchmarks/ground-truth"),
    ("GET", "/api/deployments/benchmarks/report/markdown"),
    ("GET", "/api/deployments/benchmarks/report/json"),
    ("GET", "/api/deployments/benchmarks/latex/{scenario_id}"),
    ("GET", "/api/deployments/benchmarks/ensemble"),
    ("GET", "/api/deployments/benchmarks/ensemble/markdown"),
    # Stored results of Member 2's live KinD placement-drift runs (experiments/placement_drift/results):
    # recorded observations of a separate research cluster, labelled "[Validation]", not the connected one.
    ("GET", "/api/deployments/benchmarks/live-experiments"),
}

PATHS = {path for _, path in ROUTES}

# Words a route's summary must contain to count as labelled.
LABEL_WORDS = ("sample", "fixture", "dry-run", "dry run", "demo", "benchmark", "chaos", "disruption", "scenario",
               "academic", "ground truth", "validation", "synthetic", "disclosed")

# What the runtime Python files may still mention. Nothing: the routes that read fixtures are gone.
PYTHON_ALLOWED: dict[str, set[str]] = {}

# What the page templates and scripts may still say. The chaos tab names its own dry-run endpoints.
FRONTEND_ALLOWED = {
    "app/static/js/components/deployments_scenarios_widget.js": {"benchmarks/ensemble", "chaos/benchmarks"},
}

# Declared-state views. They take declared intent from a manifest folder when one exists (named by
# RESILIENCE_MANIFEST_PATH, or the repository's own deploy/ folder in a checkout) and otherwise from the
# Deployment specs of the connected cluster (service.py documents the order). The probe in
# tests/test_runtime_fixture_reachability.py runs in a checkout, so these routes open the repository's
# manifests there; the packaged extension image ships no deploy/ folder and reads the cluster instead.
DECLARED_STATE_PATHS = {
    "/api/deployments/manifests",
    "/api/deployments/manifests/services",
    "/api/deployments/manifests/services/{service_name}",
    "/api/deployments/manifests/compliance",
    "/api/deployments/drift",
    "/api/deployments/drift/violations",
}
