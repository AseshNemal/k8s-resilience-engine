# Component Interface Ownership

Who can edit what in the web UI layer, so each member can move independently without
touching another member's files. This mirrors the engine-folder ownership already
described in `README.md` / `docs/PROJECT_CONTEXT.md`, extended to the presentation layer
added on top of the existing sample APIs.

## Member 1 — Workflow Dependency Modeling

- `app/engine/workflow_dependency_modeling/`
- `app/api/workflows.py`
- `app/web/workflows.py`
- `app/templates/components/workflows.html`
- `app/static/js/components/workflows.js`
- `app/static/css/pages/workflows.css`
- `tests/workflow_dependency_modeling/`

## Member 2 — Deployment Topology Modeling

- `app/engine/deployment_topology_modeling/`
- `app/api/deployments.py`
- `app/web/deployments.py`
- `app/templates/components/deployments.html`
- `app/static/js/components/deployments.js`
- `app/static/css/pages/deployments.css`
- `tests/deployment_topology_modeling/`

## Member 3 — Failure Impact Prediction

- `app/engine/failure_impact_prediction/`
- `app/api/failure_impacts.py`
- `app/web/failure_impacts.py`
- `app/templates/components/failure_impacts.html`
- `app/static/js/components/failure_impacts.js`
- `app/static/css/pages/failure_impacts.css`
- `tests/failure_impact_prediction/`

## Member 4 — Recommendation Evaluation and Validation

- `app/engine/recommendation_evaluation_validation/`
- `app/api/recommendations.py`
- `app/api/validations.py`
- `app/web/recommendations.py`
- `app/templates/components/recommendations.html`
- `app/static/js/components/recommendations.js`
- `app/static/css/pages/recommendations.css`
- `tests/recommendation_evaluation_validation/`

## Shared files — require team review before changing

- `app/main.py`
- `app/web/dashboard.py`, `app/web/templates.py`
- `app/templates/base.html`, `app/templates/dashboard.html`
- `app/templates/partials/`
- `app/static/css/variables.css`, `reset.css`, `layout.css`, `components.css`, `dashboard.css`
- `app/static/js/api.js`, `ui.js`, `app.js`, `components/dashboard.js`
- `app/shared/schemas/`
- `requirements.txt`

The Overview dashboard (`/`) is shared infrastructure — no single member owns it, so
changes there should be coordinated the same way as the other shared files above.
