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
```

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
checking and concentration metrics) have been created. Members 1–3 still only have empty
`service.py` stubs — no real algorithms yet.

Latest test result: 9 passed, 0 failed (`python -m pytest -v`). The earlier floating-point
assertion failure was fixed with `pytest.approx`.

## Real data only

- **Real data only at runtime.** No route, page, dashboard, prediction, recommendation, approval or
  validation may load sample, demo or fixture data in place of live evidence. If the cluster (or any
  other required evidence) cannot be read, fail with an explicit `EvidenceError` code
  (`app/shared/evidence_errors.py`), never a substituted answer. `sample_data/`, the `get_sample_*`
  loaders and `experiments/` are for automated tests and research runs only; research-only HTTP routes
  live in `tests/support/research_routes.py` and are never mounted by `app.main`.
  `tests/test_no_sample_runtime.py` enforces this. Unknown facts stay `None`; never default them
  (no `accuracy=1.0`, `restart_count=0`, `zone="default-zone"`, invented nodes or capacities).
- Unit tests must not read the developer's kubeconfig: use `tests/support/fake_cluster.py` and the
  `use_cluster` fixture; real-cluster tests are opt-in (`@pytest.mark.live_cluster`,
  `RESILIENCE_TEST_KUBECONFIG`).
