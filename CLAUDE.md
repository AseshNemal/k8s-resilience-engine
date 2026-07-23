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
task breakdowns live in `README.md` — read it before working on any `app/engine/*` component.

## Current State

This is an early-stage scaffold, not a working system yet:

- All `app/engine/*/service.py` files are empty stubs — no algorithms are implemented.
- `app/api/*.py` route handlers currently return hardcoded sample dicts (matching the
  planned JSON contracts from the README) instead of calling engine services.
- `app/api/recommendations.py` is an empty file, but `app/main.py` already imports it and
  calls `app.include_router(recommendations.router)` — the app will fail to start until
  this router is implemented.
- `app/core/`, `app/shared/schemas/`, and `app/integrations/*` are empty placeholders
  (no config, logging, Pydantic schemas, or Kubernetes/Jaeger/Prometheus clients exist yet).
- No test suite, sample_data JSON fixtures, or Kubernetes manifests exist yet, despite being
  described in the README's planned repository structure.

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

# Tests (pytest is in requirements.txt; no tests exist yet)
pytest
pytest tests/path/to/test_file.py::test_name   # single test, once tests exist
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
JSON payloads belong in `app/shared/schemas/` (`workflow.py`, `deployment.py`, `risk.py`,
`recommendation.py`, `validation.py` — not yet created).

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

## Team Rules (from README)

- Do not change shared schemas (`app/shared/schemas/`) without team agreement.
- Keep integration code (`app/integrations/`) separate from research algorithms (`app/engine/`).
- Use mock data when another component's real output isn't ready yet.
- Keep thresholds configurable rather than hardcoded.
- Never apply Kubernetes recommendations automatically — always require developer approval
  before an `apply` step.
