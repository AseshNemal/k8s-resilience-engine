# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A university research project (Project ID: J26-SE-311): a dependency-aware resilience
assessment and recommendation system for Kubernetes microservice deployments. It connects
business workflow dependencies with actual Kubernetes pod placement to predict whether
workflows survive worker-node failures, then recommends and validates minimal deployment
changes.

The system models the chain: `Workflow → Service → Pod → Node → Failure Domain`.

This is a 4-person team project split into four independently-owned research components
(see "Component Ownership" below). Full research context, data contracts, and per-component
task breakdowns live in `README.md` and `docs/PROJECT_CONTEXT.md` — read them before working
on any `app/engine/*` component. `AGENTS.md` carries the same architecture rules for
non-Claude coding agents; keep it in sync with this file.

## Current State

This is an early-stage scaffold, not a working system yet:

- Member 1's `app/engine/workflow_dependency_modeling/service.py` is still mostly a fixture loader
  (`get_sample_*` over `sample_data/*.json`, validated against `app/shared/schemas/`).
  Members 2 and 3 have real algorithms (merged from `main`): Member 2's manifest parsing, selector-based
  Service → Pod → ReplicaSet → Deployment resolution, concentration and drift analysis and graph builder;
  Member 3's node-failure prediction, portfolio evaluation and validation experiments.
- Member 4's `app/engine/recommendation_evaluation_validation/` has real logic beyond the
  fixture: `candidate_generator.py` turns `REPLICA_CONCENTRATION` root causes into
  `TOPOLOGY_SPREAD` candidates, `feasibility_analyzer.py` checks replica/node minimums,
  `metrics.py` computes replica concentration, `simulator.py` + `virtual_model.py` run the
  what-if simulation on a deep-copied `DeploymentGraph` (never mutating the original), and
  `service.py` ties them together as `evaluate_first_recommendation()` → `SimulationResult`.
  Beyond that, Member 4 now has: candidate strategies `TOPOLOGY_SPREAD`, `REPLICA_INCREASE` (single-replica
  services only) and `POD_ANTI_AFFINITY` (hardens existing soft anti-affinity) judged by one constraint search
  (`strategy_feasibility.py`); ranking (`ranking.py`) and bounded pairs (`combinations.py`) that call Member 3's
  `predict_failure_impact`; a YAML proposal generator (`yaml_proposal.py`, minimal Deployment patches, refused for
  anything not verified feasible and ranked) whose text the approval gate generates itself and re-checks when a
  request is decided; and a validation run (`validation/`) that compares a fixed prediction with a controlled
  failure in a TEST environment through an injected executor. **No real executor exists**: only the test double
  in `tests/support/member4_test_cluster.py` can run it, so nothing has been validated against a real cluster.
  The offline harness and benchmark (`tests/support/member4_*.py`, `experiments/member4_offline_benchmark/`,
  see `docs/MEMBER4_OFFLINE_VALIDATION.md`) are test/research only and stamp a non-live evidence source the
  approval gate refuses.
- `app/api/*.py` routes serve live evidence only. Topology comes from `discovery.py` (Member 2),
  which reads the connected cluster through `app/integrations/kubernetes/client.py` and returns the
  unchanged `DeploymentGraph` inside a status envelope (`/api/deployments/discovery`); failure
  prediction (`/api/failure-impacts/runtime`) needs a frozen workflow plus that topology and refuses
  when either is missing or incomplete; recommendations are computed live (`/api/recommendations/live?workflow_id=…`,
  `live_pipeline.py`: Member 4's generator, feasibility check and simulator over Member 3's runtime inputs, with
  `confidence` defined as evidence coverage) and report `RECOMMENDATION_EVIDENCE_UNAVAILABLE` when no workflow is
  named. YAML proposals are generated for an approvable candidate; validation reports are produced only by the offline run. The `/sample`, demo, fixture-simulation and Jaeger-seed routes were
  removed from the product (the deployments page has no sample toggle, the failure-impact page no fixture
  modes). Member 2's scripted chaos/benchmark dry-run routes remain, labelled; see the real-data rule below.
  Declared intent comes from, in order: a manifest path the caller names, `RESILIENCE_MANIFEST_PATH` (set on
  the backend, never from a request), the repository's own manifests, then the connected cluster's own
  Deployment specs, which the same manifest parser reads (`service.py`, `_declared_target`). A cluster that
  declares nothing answers `DECLARED_STATE_UNAVAILABLE`, never 100% compliance.
- `app/shared/schemas/` and `sample_data/` are complete for all five contracts (workflow,
  deployment, failure_impact, recommendation, validation); `sample_data/` is test-only.
  `app/integrations/kubernetes/` and `app/integrations/jaeger/` exist; `app/core/` is minimal config.
  The Kubernetes client lists nodes, pods, deployments, services and replicasets (read-only). It is a
  small httpx client (`app/integrations/kubernetes/rest.py`); the heavy `kubernetes` package is not a
  dependency and a test keeps it that way. TLS is always verified (`tls-server-name` is honoured);
  `insecure-skip-tls-verify`, `proxy-url`, basic auth and credential plugins (`exec`, `auth-provider`) are
  refused, never run. The host helper (`extension/helper`, Go) likewise speaks plain `net/http`, without
  client-go; both read the same backend kubeconfig, pinned by `tests/fixtures/tls/backend-kubeconfig.yaml`.
  The extension's role grants exactly those five (`deploy/rbac/resilience-readonly.yaml`, mirrored in
  `extension/helper/internal/rbac/rbac.go` and checked against each other by a Go test). Member 2's per-namespace
  pipeline (`service.py`, `collect_live_cluster`) uses services/replicasets: the live parser and the Service
  resolver read through `KubernetesApiSource.list_objects`, so they need no Kubernetes client package.
  `discovery.py` (what the extension pages call) uses the same selector resolver, so an application whose
  Deployments are named differently from their `app` label (Istio Bookinfo's `productpage-v1`) resolves
  completely. Pods that no Service selects are named in a `POD_SELECTED_BY_NO_SERVICE` warning and do not make
  the result partial. The rules: a graph service is a Kubernetes Service with a selector, its pods
  are those whose labels contain every selector pair, and a pod's Deployment comes from owner references.
  Anything unresolvable is reported, never guessed.
- `tests/test_sample_endpoints.py` checks that the research fixtures still validate against the
  contracts (through the research-only app in `tests/support/`, not the product).

When asked to "run the app" or "add tests", check whether the relevant piece has actually
been built yet rather than assuming the README's planned structure is already in place.

## Commands

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run the app (dashboard at /, docs at /docs, health at /api/health)
uvicorn app.main:app --reload

# Tests — use `python -m pytest`, not bare `pytest`: there's no pytest.ini/pyproject.toml,
# so plain `pytest` won't put the project root on sys.path and `import app` will fail.
python -m pytest
python -m pytest tests/test_live_evidence_api.py  # a single file
node --test tests/frontend/connection-state.test.mjs tests/frontend/api.test.mjs  # page-script logic
node --test extension/ui-src/*.test.mjs                                          # extension page scripts
(cd extension/helper && go test ./... -count=1)                                  # Go host helper (RBAC, flow)

# Docker Desktop extension: install, update, uninstall, status, check, clean (add --dry-run to preview).
# The logic is scripts/extension_manage.py; the .sh (macOS/Linux) and .cmd (Windows) files only launch it.
# The same holds for scripts/cluster-up and scripts/extension-cluster-access (cluster_up.py, extension_cluster_access.py):
# Python behind .sh/.cmd launchers, so nothing needs bash and Windows runs them directly.
./scripts/extension-manage.sh update
```

There is no configured linter, formatter, or `pyproject.toml`/`pytest.ini` in this repo yet.

## Architecture

FastAPI app (`app/main.py`) serving both a Jinja2 dashboard and a JSON API, structured as a
pipeline of four sequential components:

1. **Workflow Dependency Modeling** (`app/engine/workflow_dependency_modeling/`) — combines
   business workflow definitions with OpenTelemetry/Jaeger traces to classify service
   dependencies as mandatory/optional/supporting and build a validated `WorkflowGraph`.
2. **Deployment Topology Modeling** (`app/engine/deployment_topology_modeling/`) — resolves
   the Kubernetes ownership chain (`Service → Pod → ReplicaSet → Deployment → Node`) from
   manifests + live cluster state to build a `DeploymentGraph`, detecting replica
   concentration and placement drift.
3. **Failure Impact Prediction** (`app/engine/failure_impact_prediction/`) — takes
   `WorkflowGraph` + `DeploymentGraph`, simulates worker-node failures, and computes service
   survival, workflow survival, and blast radius into a `FailureImpactReport`.
4. **Recommendation Evaluation & Validation** (`app/engine/recommendation_evaluation_validation/`)
   — takes the `FailureImpactReport` + `DeploymentGraph`, generates candidate deployment
   changes (redistribute replica, anti-affinity, topology spread, replica increase), runs
   what-if simulation against a virtual deployment copy, ranks candidates, emits Kubernetes
   YAML, and validates predicted vs. observed improvement after developer-approved apply.

Each component is a strict pipeline stage — component N only consumes the prior component's
JSON output, never reaches backward into its internals. Shared Pydantic contracts for these
JSON payloads live in `app/shared/schemas/` (`base.py`, `workflow.py`, `deployment.py`,
`failure_impact.py`, `recommendation.py`, `validation.py`), loaded via `app/shared/sample_loader.py`.

Supporting layers:
- `app/api/` — one FastAPI router per component, mounted with a `/api/<component>` prefix in
  `app/main.py`. Routers are the only layer that should import from `app/engine/`.
- `app/integrations/` — external system clients (`kubernetes/`, `jaeger/`, `prometheus/`),
  isolated from the research algorithms in `app/engine/`.
- `app/templates/` + `app/static/` — Jinja2 dashboard, plain HTML/CSS/vanilla JS (no frontend
  build step/framework).

## Component Ownership

Each `app/engine/<component>/` folder is owned by one team member (see README for full
names/registration numbers and task lists). Do not implement another member's engine
component's algorithm without coordinating — use mock/sample JSON matching the shared
contract instead when integrating against an unfinished upstream component.

## Team Rules (from README / docs/PROJECT_CONTEXT.md)

- Do not change shared schemas (`app/shared/schemas/`) without team agreement. Same goes for
  `app/main.py`, common configuration, global dependencies, cross-component signatures, and
  metric definitions — each member can freely change their own engine folder, API route,
  template/JS, and tests, but the shared surface needs team sign-off.
- Keep integration code (`app/integrations/`) separate from research algorithms (`app/engine/`).
- Use mock/sample JSON when another component's real output isn't ready yet — in tests only, never
  behind a runtime route (see the real-data rule below).
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
- Never mutate the original `DeploymentGraph` during what-if simulation — always operate on a
  deep copy (see `virtual_model.py`'s `create_virtual_deployment`).
- Do not duplicate another member's research algorithm — call their component's public
  `service.py` interface instead (e.g. Member 4 should call Member 3's evaluator for
  blast-radius recalculation rather than reimplementing it).
- Keep algorithms deterministic and explainable; avoid ML unless there's a justified dataset
  and research question.
- Use `pytest.approx` (not exact equality) for any calculated floating-point assertion.
- Keep thresholds configurable rather than hardcoded.
- Never apply Kubernetes recommendations automatically — always require developer approval
  before an `apply` step.
- Don't claim experimental validation before controlled experiments are actually run.
