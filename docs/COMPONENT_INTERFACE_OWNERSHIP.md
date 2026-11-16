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
- `app/static/js/failure-observation.js` (the "Watch a real failure" card; see below)
- `tests/recommendation_evaluation_validation/`

**Member 4 content on Member 3's page.** `failure_impacts.html` hosts a Member 4 card ("Watch a real failure", added 2026-11-17): one
`<section>` with `data-role="observation-panel"`, one `<script>` tag, and a `.fo-*` block at the end of `failure_impacts.css`. Member 3's
script and engine do not know about it. Member 3 owns the rest of the page and may move the card, but should keep its three parts together.
See `docs/CHANGES.md`.

## Tracing infrastructure — shared, independent of Members 1–4

A read-only layer that provides a trace service and its health to any component; it analyses no traces and builds no
graph. See `docs/TRACING_INFRASTRUCTURE.md`. Member 1 may read from it as a trace source; Members 2–4 do not depend on it.

- `app/integrations/tracing/`
- `extension/tracing_routes.py`
- `extension/ui-src/tracing-panel.js`, `extension/ui-src/trace-service.js`
- `extension/helper/internal/tracesvc/`
- `tests/tracing/`, `tests/docker_extension/test_tracing_routes.py`

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
