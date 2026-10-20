# AGENTS.md

## Purpose

This repository is a four-member research prototype for dependency-aware workflow-level failure-impact assessment and validated Kubernetes resilience recommendations.

## Required Reading

Read `docs/PROJECT_CONTEXT.md` before editing.

## Final Component Folders

```text
app/engine/
├── workflow_dependency_modeling/
├── deployment_topology_modeling/
├── failure_impact_prediction/
└── recommendation_evaluation_validation/
```

Do not rename these folders without explicit approval.

## Architecture Rules

- FastAPI is the backend and application server.
- Jinja2, HTML, CSS, and vanilla JavaScript form the frontend.
- There is no separate React frontend.
- API routes validate input, call `service.py`, and return shared schemas.
- Research logic stays inside the owning component folder.
- Shared Pydantic schemas are integration contracts.
- Other components should use the owning component's public `service.py` interface.
- Member 4 must call Member 3 for failure-impact recalculation rather than duplicate that algorithm.
- Never auto-apply Kubernetes YAML.
- Developer approval is required before cluster changes.

## Validation

Run:

```bash
python -m pytest -v
node --test extension/ui-src/*.test.mjs tests/frontend/*.test.mjs
(cd extension/helper && go test ./... -count=1)
```

Docker Desktop extension: `./scripts/extension-manage.sh install|update|uninstall|status|check|clean`
(add `--dry-run` to preview; it never touches the cluster or its access objects). The logic is
`scripts/extension_manage.py`; `extension-manage.sh` (macOS/Linux) and `extension-manage.cmd` (Windows)
only launch it. The Kubernetes access is plain HTTP on both sides: `app/integrations/kubernetes/rest.py`
(httpx; no `kubernetes` package) and `extension/helper/internal/kube` (net/http; no client-go). TLS is always
verified; `insecure-skip-tls-verify`, `proxy-url`, basic auth and credential plugins are refused, never run.

Run the application:

```bash
uvicorn app.main:app --reload
```

Expected routes:

```text
/
 /docs
 /api/health
 /api/deployments/live
 /api/deployments/discovery
 /api/failure-impacts/runtime
 /api/recommendations/live
```

## Current Status

The shared FastAPI/Jinja2 foundation, Pydantic schemas, sample JSON pipeline, service-backed
endpoints, tests, and Member 4's topology-spread candidate generation (with feasibility
checking and concentration metrics) have been created. Members 2 and 3 now have real algorithms (merged
from `main`): Member 2's manifest parsing, selector-based Service resolution, drift analysis and graph
builder, and Member 3's node-failure prediction and validation experiments. Member 1's service is still
mostly a fixture loader.

Latest test result (2026-10-21, `integration/team-sync`): 1303 passed, 2 skipped, 0 failed (`python -m pytest`);
151 Node tests and every Go package pass. Floating-point assertions use `pytest.approx`.

The extension's read-only role grants `get`/`list` on exactly nodes, pods, services, deployments and
replicasets. Member 2's per-namespace pipeline reads them through `KubernetesApiSource.list_objects` (no Kubernetes client
package); `discovery.py` still attributes pods by `app` label, and moving it onto the selector resolver is
the pending follow-up. Declared intent comes from a named manifest path, `RESILIENCE_MANIFEST_PATH`, the
repository's manifests, or else the cluster's own Deployment specs (`service.py`, `_declared_target`).

## Real data only

- **Real data only at runtime.** No route, page, dashboard, prediction, recommendation, approval or
  validation may load sample, demo or fixture data in place of live evidence. If the cluster (or any
  other required evidence) cannot be read, fail with an explicit `EvidenceError` code
  (`app/shared/evidence_errors.py`), never a substituted answer. `sample_data/`, the `get_sample_*`
  loaders and `experiments/` are for automated tests and research runs only; research-only HTTP routes
  live in `tests/support/research_routes.py` and are never mounted by `app.main`. The one exception is
  Member 2's scripted chaos/benchmark dry-run routes (`/api/deployments/chaos/*`, `/benchmarks/*`): they stay
  mounted, labelled as dry-run, and are named in `tests/support/labelled_research_routes.py`, so a new
  sample, demo or benchmark route still fails the guards. `tests/test_no_sample_runtime.py` enforces this. Unknown facts stay `None`; never default them
  (no `accuracy=1.0`, `restart_count=0`, `zone="default-zone"`, invented nodes or capacities).
- Unit tests must not read the developer's kubeconfig: use `tests/support/fake_cluster.py` and the
  `use_cluster` fixture; real-cluster tests are opt-in (`@pytest.mark.live_cluster`,
  `RESILIENCE_TEST_KUBECONFIG`).
