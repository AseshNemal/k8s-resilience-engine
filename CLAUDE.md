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

- Members 1–3's `app/engine/*/service.py` currently only have a `get_sample_*` function that
  loads a fixture from `sample_data/*.json` and validates it against the matching
  `app/shared/schemas/` model — no real algorithms (trace parsing, graph building, node-failure
  simulation, etc.) are implemented yet for those three components.
- Member 4's `app/engine/recommendation_evaluation_validation/` has real logic beyond the
  fixture: `candidate_generator.py` turns `REPLICA_CONCENTRATION` root causes into
  `TOPOLOGY_SPREAD` candidates, `feasibility_analyzer.py` checks replica/node minimums,
  `metrics.py` computes replica concentration, `simulator.py` + `virtual_model.py` run the
  what-if simulation on a deep-copied `DeploymentGraph` (never mutating the original), and
  `service.py` ties them together as `evaluate_first_recommendation()` → `SimulationResult`.
  Ranking, YAML generation, and calling Member 3's evaluator are still unbuilt.
- `app/api/*.py` route handlers are wired end-to-end (router → engine `get_sample_*` service →
  `sample_loader` → schema-validated response) for the `/sample` endpoints, but every response
  is still the same fixture data regardless of input — there's no request-driven behavior yet.
- `app/shared/schemas/` and `sample_data/` are complete for all five contracts (workflow,
  deployment, failure_impact, recommendation, validation). `app/core/` and
  `app/integrations/*` are still empty placeholders (no config, logging, or
  Kubernetes/Jaeger/Prometheus clients exist yet).
- `tests/test_sample_endpoints.py` smoke-tests that every `/api/*/sample` endpoint returns
  200 with a JSON body — it does not assert on response shape/values yet.

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
python -m pytest tests/test_sample_endpoints.py::test_sample_endpoint_returns_success  # single test
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
- Use mock/sample JSON when another component's real output isn't ready yet.
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
