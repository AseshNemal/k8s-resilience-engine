"""The member-owned research routes that stay mounted, and exactly what they may contain.

Components 2 and 3 keep a few routes that run on a fixture or a dry-run scenario instead of the connected
cluster (the sample deployment, the chaos scenario runner, the academic benchmarks, the Component 3
contract-fixture simulations). They stay in the product for the viva demonstrations, with two conditions:

* each one is named here, so a *new* sample, demo or benchmark route still fails the guards;
* each one labels itself as sample, fixture, dry-run or benchmark data in its OpenAPI summary or in the
  page text, so it can never be mistaken for evidence about the connected cluster.

Everything else must stay real-data-only (tests/test_no_sample_runtime.py,
tests/test_live_evidence_api.py).
"""

# (method, path) of the routes that serve fixture or dry-run data.
ROUTES = {
    ("GET", "/api/deployments/sample"),
    ("GET", "/api/deployments/sample/analysis"),
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
    ("GET", "/api/failure-impacts/sample"),
    ("GET", "/api/failure-impacts/demo-deployment"),
    ("GET", "/api/failure-impacts/simulate"),
    ("GET", "/api/failure-impacts/portfolio/simulate"),
}

PATHS = {path for _, path in ROUTES}

# Words a route's summary must contain to count as labelled.
LABEL_WORDS = ("sample", "fixture", "dry-run", "dry run", "demo", "benchmark", "chaos", "disruption", "scenario",
               "academic", "ground truth", "validation", "synthetic", "disclosed")

# What the runtime Python files may still mention. Only these names, only in these files.
PYTHON_ALLOWED = {
    "app/api/deployments.py": {"get_sample_"},
    "app/api/failure_impacts.py": {"get_demo_", "get_sample_", "predict_demo_"},
}

# What the page templates and scripts may still say. The sample and fixture modes are offered by name and
# labelled as such on screen ("Sample Data", "Contract Fixtures").
FRONTEND_ALLOWED = {
    "app/templates/components/deployments.html": {"Sample Data"},
    "app/templates/components/failure_impacts.html": {"Contract fixtures"},
    "app/static/js/components/deployments.js": {"/sample", "Sample Data", "sample data"},
    "app/static/js/components/deployments_scenarios_widget.js": {"benchmarks/ensemble", "chaos/benchmarks"},
    "app/static/js/components/failure_impacts.js": {"/demo-deployment", "/failure-impacts/simulate", "Contract Fixtures"},
}

# Declared-state views. When no declared-state source is named they read the repository's own test-workload
# manifests (deploy/test-workloads) as the declared intent to compare the live cluster against. That is
# declared intent, not cluster data, and it exists only in a repository checkout: the packaged extension
# image ships no deploy/ folder, and there these routes answer DECLARED_STATE_UNAVAILABLE (409).
DECLARED_STATE_PATHS = {
    "/api/deployments/manifests",
    "/api/deployments/manifests/services",
    "/api/deployments/manifests/services/{service_name}",
    "/api/deployments/manifests/compliance",
    "/api/deployments/drift",
    "/api/deployments/drift/violations",
}
