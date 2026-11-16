# Changes other members should know about

Newest first. Each entry says whose area it touches, what it changed outside its owner's folder, and what it did **not** change.
Shared schemas (`app/shared/schemas/`), `app/main.py` and every other member's algorithm are untouched unless an entry says so.

## 2026-11-17 — Member 4: Keep refreshes the page, and "Watch a real failure"

Branch `feat/production-readiness`, committed together with the earlier work listed below. Extension build 0.1.710.

| Area | Owner | What changed |
|---|---|---|
| `app/engine/recommendation_evaluation_validation/observation/` (new) | Member 4 | Read-only watch of a worker failure that the **developer** causes. Records the model's prediction for every worker first, then what the cluster does. |
| `app/api/recommendations.py` | Member 4 | Routes `POST /api/recommendations/failure-observations`, `GET .../current`, `GET .../`, `POST .../{id}/stop`. Calls Member 3's public `build_cluster_failure_overview`. |
| `alembic/versions/e5f7a2c91b36_…` | shared persistence | **Additive** table `failure_observations`. `tests/test_migrations.py` now pins this head. |
| `app/templates/components/failure_impacts.html`, `css/pages/failure_impacts.css` | **Member 3's page** | A "Watch a real failure" card and one `<script>` tag were added below the node list; the CSS block `.fo-*` is appended at the end. The card needs nothing from Member 3's script or engine. (The same commit also swaps the page's placement table for the shared placement map; see below.) |
| `static/js/failure-observation.js` (new) | Member 4 | Mounts itself into `[data-role="observation-panel"]`; does nothing until the person presses Start. |
| `static/js/recommendation-apply-test.js`, `recommendation-action.js` | Member 4 | After **Keep** the page re-reads the cluster (risks and changes are recalculated) and the dock offers "Choose another change" beside Reset. |

Behaviour worth knowing:

- This app **stops nothing and writes nothing** here. Node readiness is read through a gateway built with writes off.
- The comparison covers one failed node and service readiness only. Workflow outcomes are shown as **not observed**, never as survived.
- Nothing runs in the background: the record advances when the page asks. An unreadable cluster is *unknown*, never "down" or "recovered".
- Verified on in-memory doubles and by a read-only watch of the real Bookinfo cluster. **No worker has been stopped and no failure has been observed for real.**
- Tests: `tests/recommendation_evaluation_validation/test_failure_observation.py`, `tests/frontend/failure-observation.test.mjs`.

**Review follow-ups (PR 49):**

- **Member 3, `failure_impact_prediction/service.py`:** `load_application_snapshot` now leaves *incomplete* services out of the assessment (a service with a replica on a node discovery does not model, for example the control plane). Before, their truncated placement was scored, so a service could be reported lost while a replica elsewhere survived, contradicting the page's "not assessed" notice. They are still named in `topology.incompleteServices`. Test: `test_a_service_with_a_replica_on_an_unmodelled_node_is_not_assessed_from_its_truncated_placement`.
- **Member 4:** a watch is bound to the cluster it started on (`cluster.clusterUid`). A different or unidentifiable cluster is never read as the watched one (`clusterMatch` `other` / `unknown`), and a watch cannot start while the cluster cannot be identified.

For Member 3 and Member 2: on the discovery path the overview's `nodes[].ready` is `None` (unknown), so Member 4 reads node readiness itself. If the overview later reports it, the watch can use it.

## Earlier work in the same working tree (details in `CLAUDE.md`, "Current State")

- **Placement map** (`static/js/placement-map.js`, `css/placement-map.css`, `placement_view.py`): shared by Assessment, Failure impact and service-level Recommendations. Draws only what it is given; simulated cells are dashed. **Member 3:** on the Failure impact page it replaces the old placement table, so `failure_impacts.js` and `failure_impacts.html` changed (the engine and `/overview` did not).
- **Action dock** (`static/js/recommendation-action.js`): the selected change has one next action (review and approve, apply, keep or reset). With no reviewed workflow there is no approve or apply, only "Review a workflow".
- **Assessment redesign** and **deployment-level recommendations** (`engine/assessment/`, `deployment_recommendations.py`): service-level suggestions when no workflow is reviewed. Read-only; they cannot authorise Apply.

## 2026-11-16 — Member 1: workflow test evidence and the workflow manager (committed, `a35ab54`)

- Workflows can combine **Add Manually**, **Record Activity** and **Add Test Evidence**. Imports never upgrade an observed call to mandatory and cannot claim experimental validation.
- New endpoints under `/api/workflows/management`: `GET /test-evidence`; `POST /test-evidence/import`, `/test-evidence/run`, `/test-evidence/{id}/refresh`. No new migration (existing `workflow_management_revisions` storage).
- Exporters for the application's own Pytest/Playwright suites are in `app/static/test-integrations/`.
- An **operational review** (`docs/WORKFLOW_EVIDENCE_POLICY.md`) is what lets a workflow drive approve and Apply. Without one, Recommendations stays read-only.
- Unverified against a real application: customer test suites with real trace propagation, and concurrent-traffic attribution.

## Open issues for other members

| For | Issue | Where |
|---|---|---|
| Member 3 | A Pending pod makes portfolio prediction raise `CrossLayerGraphError` (the check is still in `cross_layer_graph.py`). | `docs/HANDOFF_MEMBER3_PENDING_POD_CROSS_LAYER_GRAPH.md` |
| Member 2 | Reported 2026-10-22, not re-checked since: an unreadable node capacity becomes `0.0` in `analyze_live_topology` but `None` in `discover_live`. | `docs/HANDOFF_MEMBER2_UNKNOWN_CAPACITY.md` |
