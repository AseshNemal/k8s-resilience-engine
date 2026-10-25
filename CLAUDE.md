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
  failure in a TEST environment through an injected executor. The run reverts what it applied and its verdict is
  idempotent. `validation/kubernetes_executor.py` holds the safety decisions of a real executor (cluster pinned by
  `kube-system` UID and never the docker-desktop research cluster, YAML checked against live Deployments, writes off
  unless armed, only a stopped node counts as a failure, observed settling) over a `ClusterGateway` protocol, and
  `validation_runs` stores each experiment with predicted and observed apart. A gateway for one designated test
  cluster (`app/integrations/kubernetes/validation_gateway.py`, read-only by default, refuses the default kubeconfig), a kind
  worker fault injector (`app/integrations/kind/`: `docker kill`, its own authorization per kill, durable recovery records) and
  an HTTP workflow probe (`validation/workflow_probe.py`) now exist, composed in `experiments/member4_real_validation/`, but they
  have only ever run against in-memory doubles (`tests/support/member4_*`): **nothing has been validated against a real
  cluster, no cluster has been created, no node has been stopped, and no request has been sent to a real application.** The
  resource assessment for the developer's machine is INSUFFICIENT, so the test cluster is not to be created until that changes.
  The protocol is `docs/MEMBER4_REAL_VALIDATION_PROTOCOL.md`; the gates, procedure and recovery are in
  `docs/MEMBER4_KIND_VALIDATION_RUNBOOK.md`. Member 4 takes Member 2's node and intent facts from `analyze_live_topology` and reads node
  taints itself (`live_scheduling_evidence.py`), and when Member 1 has no frozen workflow it can evaluate a workflow a person **declared**
  (`declared_workflow.py`, `/api/recommendations/declared`), labelled `declared-workflow` and never approvable by the production gate. A
  separate, narrower **research approval tier** (`approval/research.py`, developer-approved 2026-10-23) lets a named person approve such a
  recommendation for validation on a designated test cluster only, bound to the declaration's digest; its records say `workflowBasis: DECLARED`
  and are conditional on the declaration. The production gate, its routes and its tests are unchanged. See `docs/MEMBER4_INPUT_SOURCES.md`.
  Apply is followed through as one persisted operation (`application/rollout.py`, `analysis.py`, `live_analyser.py`, `operation.py`; additive migration `f4b91c2d7e03`): the
  rollout is watched from the Deployments' own generation and replica counts, the failure impact is recalculated from the freshly observed topology and compared with a
  baseline stored before the change, Reset has its own rollout, restoration check and analysis, and an "Applied change" panel (`recommendation-operation.js`, outside the
  re-rendered region) shows it from Apply to Reset. See `docs/MEMBER4_APPLY_ROLLOUT_ANALYSIS.md`.
  An applied change belongs to the cluster it was applied to (`change.cluster.clusterUid`): `ApplicationService.status()` and `ApplyTestService.read()` return
  `clusterMatch` (`same` | `other` | `unknown`), a change on another cluster blocks and offers nothing here (each cluster is independent; Reset needs the original cluster
  connected), and a cluster UID that cannot be read just now is `unknown` and answers a retryable `CLUSTER_IDENTITY_UNKNOWN`, never `CLUSTER_CHANGED`. While an Apply-and-test run is
  busy its panel polls the read-only `GET /api/recommendations/apply-tests/{run_id}/live` (`ApplyTestService.live`, gateway `read_live_activity` / `read_node_states`): per target
  Deployment its replicas and each pod's node, state and version (new or old template), plus node status, usage and a recent-steps timeline. It never writes, and says why when it has nothing to show.
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
  Deployments, which the same manifest parser reads (`service.py`, `_declared_target`): per Deployment the
  manifest last applied with kubectl (`kubectl.kubernetes.io/last-applied-configuration`, untouched by
  `kubectl scale`), else its live spec (`_declared_document`). A cluster that
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
  The extension's role is FULL ACCESS (`*/*/*`) to the connected research cluster since 2026-10-23, developer-approved so that Apply and Reset can write
  (`deploy/rbac/resilience-readonly.yaml`, file and object names kept so an install upgrades in place; mirrored in `extension/helper/internal/rbac/rbac.go`
  and checked against each other by a Go test). The backend no longer refuses a credential that can write (`extension/cluster_connection.py`,
  `_verify_access` only requires the reads the app needs); it still writes ONLY through `application.ApplicationService`. Member 2's per-namespace
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

- **Fault-injection validation loop** (`validation_loop/` under Member 4, routes `/api/recommendations/validation-loop/*`, the "Prove it on a test
  cluster" step of the Recommendations page). One run approval (a digest of the reviewed plan, no person's name) covers a bounded run: Smoke, Quick
  (default, preliminary) or Full Research (measured baseline required). Each candidate is applied once, each authorized worker is stopped in turn
  by `docker kill`, recovery is verified after every stop, and the Deployments are put back; a durable kill budget (`validation_loop_runs.kills_used`)
  bounds the number of stops. It runs on a background thread with all state in the database (migration `a3c6e1f90b52`); after a backend restart
  it only **recovers** (restores workers, reverts Deployments) and never resumes or repeats a kill. Docker is reached only through the
  `docker-proxy` service (`app/integrations/docker/`, the only container with the Docker socket; it authorizes the CONTAINER by name, kind labels
  and an allow-list, and trims every answer). The Docker Desktop cluster is blocked unless a run carries an explicit acknowledgement and a
  `ResearchClusterAuthorization` for its exact UID. Runs and recovery need `RESILIENCE_ALLOW_CLUSTER_WRITES=1`. A recommendation computed on a
  test cluster is labelled `validation-target` and can never be approved. **It has only ever run against in-memory doubles; no real node has been
  stopped by it, the disposable kind cluster is not created by the product (a manual, separately approved step), and readiness uses bounded polling,
  not a Kubernetes watch.** Design and decisions: the plan file of the branch `feat/fault-injection-validation`.

- **Recommendations page UI** (`app/templates/components/recommendations.html`, `components/recommendations.js`, `recommendation-render.js`, `css/pages/recommendations.css`):
  five steps shown one at a time (`data-step` on the flow root, `data-step-panel` on each panel; footers and the case line open a step with `data-go`), a
  Simple view that is the short answer (problem, lead fix, predicted improvement, validation status, next action) and a Research view that adds a tabbed
  workbench (ranking, reasoning, placement, every failure, post-apply validation, raw data) and the proof panel's tabbed observed data. The stepper is
  drawn from `computeSteps`: the approval step is never "done" or "the step to do" until the server says THIS change is proven on a test cluster (`/proof`);
  a decision recorded earlier is kept and shown as an earlier approval (state `recorded`), never as validation. Approval is still not gated by the proof:
  that is a UI statement only, and the approval logic and routes are unchanged. A declared workflow is labelled "research approval only", never "not
  approvable". The cluster chip says "Connected": the connection check reads what the app needs, and write permission is not checked until a change is applied.

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
  before an `apply` step. Since 2026-10-23 that step exists: `application/` (`ApplicationService`, routes `.../apply`, `.../reset`, `.../application`,
  both tiers) applies an APPROVED change to the connected cluster on a named person's request and can put the stored prior state back. It re-checks the
  evaluation, the server-generated YAML and the proposal whitelist first, applies one change at a time, and never runs on approval or on a read. A status
  request reads the cluster and advances the stored rollout and the post-change analysis (Member 3's evaluator on a freshly observed topology), but never patches;
  see `docs/MEMBER4_APPLY_ROLLOUT_ANALYSIS.md`. That analysis is a model recalculation over observed placement, not an experiment.
- Don't claim experimental validation before controlled experiments are actually run.
