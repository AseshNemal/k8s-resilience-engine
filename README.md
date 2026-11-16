# K8s Resilience Engine

A dependency-aware resilience assessment and recommendation system for Kubernetes microservices. It connects business
workflows to where pods actually run, predicts whether workflows survive a worker-node failure, recommends minimal
deployment changes, and validates them after a developer-approved apply.

```text
Workflow → Service → Pod → Node → Failure Domain
```

**Project ID:** J26-SE-311 · **Topic:** Dependency Aware Failure Domain Risk Assessment for Kubernetes Deployed Microservice Architectures
· **Stack:** FastAPI, Jinja2/HTML/CSS/vanilla JS, SQLite (extension) or PostgreSQL

## Team and components

Four sequential components; each consumes only the previous one's JSON output (contracts in `app/shared/schemas/`).

| Member | Registration | Component | Folder (`app/engine/`) |
|---|---|---|---|
| A.D. Perera | IT23236400 | 1. Workflow Discovery | `workflow_dependency_modeling/` |
| P.A.M. Minara | IT23154476 | 2. Deployment Graph and Placement Drift | `deployment_topology_modeling/` |
| K.V.D.Y.R. Karawita | IT23236882 | 3. Cross-Layer Failure Prediction | `failure_impact_prediction/` |
| L.A. Nemal | IT23236264 | 4. Recommendation and Validation | `recommendation_evaluation_validation/` |

## Run it

Python 3.11+ (developed on 3.13).

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload                        # http://127.0.0.1:8000  (API docs at /docs)
python -m pytest                                     # use `python -m`, not bare `pytest`
node --test tests/frontend/*.test.mjs extension/ui-src/*.test.mjs
```

The app reads the cluster your kubeconfig points at, read-only. It never serves sample data in place of live evidence;
if the cluster can't be read it says so. Writes (Apply, Reset) happen only on a named person's explicit request and need
`RESILIENCE_ALLOW_CLUSTER_WRITES=1`.

**Docker Desktop extension** (the normal way to use it): `./scripts/extension-manage.sh update` (Windows:
`scripts\extension-manage.cmd update`; add `--dry-run` to preview). See `docs/EXTENSION.md` and `docs/DOCKER.md`.

## Rules that matter

- Real data only at runtime; unknown facts stay `null`, never defaulted.
- No change to shared schemas or `app/main.py` without team agreement; call another member's `service.py`, never its internals.
- Never apply a recommendation automatically; never claim experimental validation before a controlled experiment has run.

## Status

Reads, prediction, ranked recommendations, approval, Apply, rollout watch, Keep/Reset and a read-only failure watch exist and
are tested on in-memory doubles. **No Apply, node failure or controlled experiment has run on a real cluster yet.**

## Docs

| Read | For |
|---|---|
| `docs/CHANGES.md` | What changed recently and in whose area (start here when pulling) |
| `docs/PROJECT_CONTEXT.md` | Research context, per-member tasks, data contracts, experiment plan |
| `docs/COMPONENT_INTERFACE_OWNERSHIP.md` | Who owns which file |
| `CLAUDE.md` / `AGENTS.md` | Detailed current state and rules for coding agents |
| `docs/EXTENSION.md`, `docs/DOCKER.md` | Install, update, troubleshooting |
| `docs/MEMBER4_*.md`, `docs/RECOMMENDATION_LIVE_DATA.md`, `docs/WORKFLOW_EVIDENCE_POLICY.md` | Recommendation, validation and workflow-evidence details |
