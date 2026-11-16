# Recovery handoff — 2026-11-16

## User-authorized source snapshot — 2026-11-16

The user requested one commit of all legitimate Kimi/Codex development work, including both handoff documents. The recovery-state statements below describe the earlier uncommitted checkout; this snapshot explicitly includes the previously ignored Markdown files. Deployment and real-cluster acceptance remain separate.

Pre-commit review found and fixed one concrete UI issue: the Apply-and-test Reset click handler could pass a browser event as the `external` policy. It now invokes Reset without forwarding that event, and the existing test supplies a click event and asserts `external: "refuse"`. Targeted command `node --test tests/frontend/recommendation-apply-test.test.mjs`: **32 passed**. The entire suite was not rerun.

Read-only preparation also inspected the update process: the installed extension reported **0.1.678**, built from `7f019d0`; `update --dry-run --keep-all` printed a proposed **0.1.679** build/update and reported that nothing changed. `extension/entrypoint.sh` runs `alembic upgrade head` on startup, so an actual extension update will migrate the persistent database automatically. A verified backup and rollback plan must therefore precede the future approved update; none was performed here.

The user redirected acceptance preparation to committing the current source before trace-identity changes were implemented. The trace-service/workload mapping and traffic-attribution limitations below remain outstanding. Do not treat this commit as fixing those gaps, completing the migration backup plan, authorizing deployment, or validating a real cluster. No extension update, persistent migration, cluster modification or fault injection was performed.

This update supersedes the interrupted handoff. It describes source code and isolated verification in this checkout, not the installed extension or a real cluster. Historical handoff copies are in `tmp/previous-agent-handoff.md` and `tmp/previous-verification-checklist.md`.

## Recovered state and authorization

- Branch: `feat/bookinfo-tracing-on-main`; HEAD: `7f019d0` (unchanged).
- At takeover: 52 tracked files modified and 12 new source/test files; the two ignored handoff documents already existed. Actual diffs were inspected, including the interrupted recommendation context, deployment-only predictor, Reset/Keep/ledger, disconnect UI, exact proposal fields and existing reviews-v2 adoption.
- All prior work was preserved. No commit, push, merge, rebase, extension installation/update, real-cluster request, Kubernetes mutation or fault injection was performed in this recovery.
- The user's pasted recovery request explicitly permits edits across the four components and shared integration contracts. The older Member-4-only ownership test was updated with an exact authorized-file list; its public-service assertion remains.
- Codebase-memory indexing and search were attempted first but returned `Transport closed`; direct source inspection was used after that failure.

## Interrupted recommendation context repaired

`evidence_context.py` now passes Member 2's already-discovered topology to Member 3's public service predictor. With no applicable frozen workflow, complete placement produces per-node failed/affected logical services. No WorkflowGraph is synthesized. Partial placement is not promoted to a reliable prediction. Workflow survival and workflow blast radius are explicitly null, and approvalAllowed is false.

`recommendation-context.js` carries the findings through its controller and `recommendation-render.js` draws the read-only table. The Recommendations page shows it when workflow analysis is unavailable. Once a declared or frozen recommendation is loaded, the deployment-only fallback is hidden so it cannot contradict the active workflow basis. The dedicated Failure impact deployment mode also has explicit null workflow metrics and refuses incomplete live topology.

## Integrated hybrid workflow management

The existing Workflow dependencies page now opens with Workflow studio. Its existing controlled-experiment and assessment workspace remains below it in an expandable section.

- Live logical-service catalog calls Member 2's `service.discover_live`, grouping by Kubernetes Service rather than container. Selecting a service shows its Deployment names, ready/declared replicas, pods, node placement and reported node capacity/zone. Unreported capacity and zone remain unknown.
- The canvas supports adding services with drag or Add, moving with drag or keyboard, drawing directional connections via a Connect port or accessible caller/dependency controls, editing/removing connections, Mandatory/Optional/Supporting/Unknown labels, removing nodes, creating/naming, duplicating and deleting workflows, and bounded undo/redo.
- Draft and reviewed saves are append-only revisions. The composite primary key enforces optimistic concurrency; stale saves return WORKFLOW_VERSION_CONFLICT. Delete adds a tombstone and retains previous versions. History can reopen a version; duplicating a historical version starts a separate history.
- Start Recording stores a durable start time, namespace and connection fingerprint. Stop freezes the end before collecting evidence; explicit refresh re-reads that same window into a new revision. Existing root-operation discovery, span parsing, trace-quality checks and interaction extraction are reused.
- Candidate workflows use the same editor. Merge preserves manual classifications, adds new connections as Unknown, records session IDs, and does not guess mappings for unmatched trace service names. Server-owned provenance includes trace IDs and span/operation references. Client-supplied proof/evidence fields are rejected.
- Saved graphs identify observed interactions separately from declared dependency classifications. Omitted observed connections between canvas services are surfaced as conflicts for review. Prior revisions remain intact.
- Save reviewed graph requires a selected entry service, explicit criticality, resolved dependency labels and reachability. Its WorkflowGraph projects Mandatory paths from the entry to mandatoryServices; Optional and Supporting remain optionalServices. Unknown classifications cannot be exported as reviewed.
- Reviewed editor graphs feed the existing DECLARED research recommendation path through Member 1's public service interface. They never masquerade as assessed/frozen production evidence. Namespace and connection binding are checked. Revision digests invalidate prior approval evidence when the declaration changes. File declarations remain supported. A latest draft/deleted revision is not supplied as an active reviewed graph; earlier versions remain in history.

## Existing end-to-end work recovered and verified locally

- Deployment-only service/node failure predictions; frozen-workflow and declared-workflow recommendation paths remain separate.
- Current/proposed replica, topology-spread and anti-affinity field comparisons before approval.
- Named Apply, read-only rollout polling and model recalculation from observed post-change placement; these are not experimental measurements.
- Read-back after-snapshots, outside-change detection, partial Reset that leaves outside fields untouched, Keep decisions and the outstanding-changes ledger.
- Connect-page three-way disconnect decision and reconnect ledger. Reset waits for the stored rollout; unreadable ledgers and ongoing resets refuse disconnect.
- Apply-and-test has the same partial-reset choice after EXTERNAL_CHANGE.
- Optional Bookinfo tracing installer adopts an existing reviews-v2, restarts its pods without owning/recreating/deleting its Deployment, and preserves replicas; Plan/Apply/failure rollback/Undo are covered with Go doubles. Generic discovery/analysis does not require Bookinfo.
- Recovery callers were repaired after noticing that the new resourceVersion guard would otherwise reuse pre-change versions. Validation revert, restart recovery and failed-Apply cleanup re-read current versions, refuse replaced targets or additional spec generations, and keep recovery records on refusal. The explicit ApplicationService Reset still compares owned fields before its version-pinned patch.

## Persistence and delivery

Pending additive migrations:

1. `c9d2f7a41e86_add_after_to_applied_changes.py` (recovered): nullable applied_changes.after.
2. `d4e8b1f60a23_workflow_management_revisions.py` (new): append-only workflow/recording revisions, composite key (kind, identity, version).

Migration tests used isolated databases. The developer's and extension's databases were not migrated. Use the existing setup/deployment migration procedure before acceptance. The static extension renderer includes the new template, script and CSS through the existing asset pipeline; its build tests pass. The extension image was not built or installed.

## Verification executed

Commands used `.venv/bin/python`; all Kubernetes sources were in-memory doubles and tests retained the repository's kubeconfig isolation.

| Verification | Actual result |
|---|---|
| One consolidated `python -m pytest -q` | 3634 passed, 19 skipped, 1 failed: obsolete Member-4-only ownership guard |
| Scope-guard correction retest | 2 passed; exact integration allowlist, public-service boundary check retained |
| Final focused Python run (management, ownership, executor/recovery, Reset, extension renderer, deployment-only predictor) | 148 passed |
| `node --test extension/ui-src/*.test.mjs tests/frontend/*.test.mjs` | 793 passed, 0 failed |
| Focused frontend retest (editor, context, renderer) | 71 passed |
| `(cd extension/helper && go test ./... -count=1)` | All 17 packages passed |
| `node --check app/static/js/workflow-editor.js`; Python compileall of new backend modules; `git diff --check` | Passed |

The full Python suite was not repeated after the ownership guard correction and final small editor/storage refinements. The affected focused tests passed; do not relabel the original consolidated run as a fully green rerun. Earlier focused results (not additional unique tests): context/prediction/migrations 48 passed; Reset/ledger/disconnect/gateway/packaging/address policy 172 passed; recovered recommendation/disconnect frontend 152 passed.

Logs: `tmp/final-python-verification.log`, `tmp/final-focused-verification.log`, `tmp/final-node-verification.log`, `tmp/final-focused-node.log`, `tmp/final-go-verification.log`.

### Browser evidence (isolated fake cluster only)

A temporary `tmp/editor_acceptance.py` server used an isolated SQLite database and an in-memory, non-Bookinfo gateway/billing application. Writes were disabled and no real kubeconfig was available. It was stopped after verification.

Observed in Chromium:
- No workflow: Recommendations shows both worker nodes and their real-to-the-double service exposure, explicitly unknown workflow metrics and no Apply button.
- Studio: add two logical services, name the workflow, set entry and criticality, connect, classify Mandatory, save reviewed, reload and reopen with name, criticality and both nodes intact.
- Recommendations discovers that reviewed graph as declared research evidence; the deployment-only fallback disappears after its workflow analysis loads.
- Save a later draft: version 2 appears and version 1 remains selectable in history.
- At 390px viewport, the settled page width stays within the viewport and Studio switches to its stacked layout; at desktop width the canvas and inspectors render.

Browser testing caught and fixed criticality text being cleared by a different field's render. A first harness run lacked apply-test tables because it created metadata before all models were imported; that was fixed only in the temporary harness and the read-only Recommendations check was repeated. These are synthetic engineering checks, not application resilience experiments.

Screenshots: `tmp/workflow-editor-initial.png`, `tmp/workflow-editor-reviewed.png`, `tmp/workflow-editor-mobile.png`. Browser snapshots/console artifacts: `tmp/editor-browser-artifacts/`.

## Limitations and real-cluster acceptance still required

- No current installed-extension version or live-cluster state was verified. Earlier handoff cluster/version/capacity claims remain historical.
- New recording controls currently use Jaeger. They read an existing tracing backend and do not install instrumentation. Manual management works without tracing.
- Correlation is a fixed time window plus matching logical service names, not a propagated recording-session tag or namespace attestation. Concurrent traffic, identical names in other namespaces/clusters, sampling, clock skew, ingestion delays and expired traces can affect attribution. Matching service names are not a proof of namespace membership. References from selected sessions can span multiple operations; dependency classification remains declared.
- Recording is bounded to 12 matching services, 8 queries per service, 100 traces per query. Query gaps and unqueried services are reported. Refresh preserves prior evidence revisions. Unmatched tracing names need a future explicit mapping mechanism; they are never guessed into logical services.
- The shared graph is service-level; Supporting projects to optional, and conditional/per-request dependency semantics are not represented. Experimental dependency proof still uses the existing controlled assessment/freeze workflow. Canvas review alone never produces that proof.
- Deleted histories are retained and readable by document ID/API, but the active workflow selector hides deleted entries. Rich graph layout, zoom and arbitrary trace-name mapping are not implemented.
- Cluster binding for editor records uses the connection fingerprint for invalidation; it is not a cluster UID attestation. Apply/Reset continue to require their existing cluster UID checks.
- Legacy applied changes without an after-snapshot cannot identify outside field drift; existing behavior reports that limit. Concurrent status updates may cause a safe version conflict and require retry. Validation recovery refuses additional spec generations instead of overwriting them and can require manual reconciliation; generation checks alone do not attribute a single spec edit after an uncertain write.
- Real tracing capture, real generic-app discovery, actual scheduling feasibility, admission/defaulting, Apply/rollout, field comparisons, resourceVersion conflicts, partial Reset, Keep/disconnect/reconnect and experimental failure/recovery remain unverified on Kubernetes. Windows/Linux platform behavior is also unverified.

Source is ready for controlled acceptance preparation, not a claim of empirical validation or production readiness. After explicit developer approval, migrate the target database and update the extension, run read-only discovery on the intended cluster, then exercise the named Apply/Reset/Keep/disconnect flow with Bookinfo and one generic application. A fault-injection experiment requires its own explicit approvals, cluster designation, resources and kill/recovery gates; do not run it automatically.

## Current changed-file inventory

This is the recovery working-tree inventory relative to `7f019d0`, including inherited Kimi changes. It does not imply every listed line was authored during recovery. The two handoff Markdown files are also explicitly included in the user-authorized source snapshot despite `.gitignore`. Verification artifacts under `tmp/` are excluded.

### Modified tracked files (66)

- `app/api/failure_impacts.py`
- `app/api/recommendations.py`
- `app/api/validation_loop_environment.py`
- `app/api/workflows.py`
- `app/engine/failure_impact_prediction/service.py`
- `app/engine/recommendation_evaluation_validation/application/__init__.py`
- `app/engine/recommendation_evaluation_validation/application/live_analyser.py`
- `app/engine/recommendation_evaluation_validation/application/models.py`
- `app/engine/recommendation_evaluation_validation/application/operation.py`
- `app/engine/recommendation_evaluation_validation/application/repository.py`
- `app/engine/recommendation_evaluation_validation/application/service.py`
- `app/engine/recommendation_evaluation_validation/apply_test/service.py`
- `app/engine/recommendation_evaluation_validation/declared_workflow.py`
- `app/engine/recommendation_evaluation_validation/evidence_context.py`
- `app/engine/recommendation_evaluation_validation/explanation.py`
- `app/engine/recommendation_evaluation_validation/live_pipeline.py`
- `app/engine/recommendation_evaluation_validation/portfolio_pipeline.py`
- `app/engine/recommendation_evaluation_validation/validation/cluster_gateway.py`
- `app/engine/recommendation_evaluation_validation/validation/kubernetes_executor.py`
- `app/engine/workflow_dependency_modeling/service.py`
- `app/integrations/kubernetes/validation_gateway.py`
- `app/shared/persistence/models.py`
- `app/shared/schemas/failure_impact.py`
- `app/static/css/pages/recommendations.css`
- `app/static/js/components/failure_impacts.js`
- `app/static/js/components/recommendations.js`
- `app/static/js/recommendation-apply-test.js`
- `app/static/js/recommendation-context.js`
- `app/static/js/recommendation-operation.js`
- `app/static/js/recommendation-render.js`
- `app/templates/components/failure_impacts.html`
- `app/templates/components/recommendations.html`
- `app/templates/components/workflows.html`
- `extension/Dockerfile`
- `extension/build_ui.py`
- `extension/cluster_connection.py`
- `extension/cluster_ingest.py`
- `extension/helper/internal/bookinfotrace/changes.go`
- `extension/helper/internal/bookinfotrace/changes_test.go`
- `extension/helper/internal/bookinfotrace/dialog.go`
- `extension/helper/internal/bookinfotrace/dialog_test.go`
- `extension/helper/internal/bookinfotrace/gates.go`
- `extension/helper/internal/bookinfotrace/helpers_test.go`
- `extension/helper/internal/bookinfotrace/plan.go`
- `extension/helper/internal/bookinfotrace/plan_test.go`
- `extension/helper/internal/traceinstall/apply.go`
- `extension/helper/internal/traceinstall/apply_test.go`
- `extension/helper/internal/traceinstall/engine.go`
- `extension/helper/internal/traceinstall/guard.go`
- `extension/helper/internal/traceinstall/resources.go`
- `extension/helper/internal/traceinstall/rollback.go`
- `extension/ui-src/connect-auto.js`
- `extension/ui-src/connect.js`
- `extension/ui-src/docker.css`
- `tests/frontend/recommendation-apply-test.test.mjs`
- `tests/frontend/recommendation-context.test.mjs`
- `tests/frontend/recommendation-operation.test.mjs`
- `tests/frontend/recommendation-render.test.mjs`
- `tests/integrations/test_validation_gateway.py`
- `tests/recommendation_evaluation_validation/test_independent_inputs.py`
- `tests/recommendation_evaluation_validation/test_kubernetes_executor.py`
- `tests/recommendation_evaluation_validation/test_live_context.py`
- `tests/recommendation_evaluation_validation/test_member_boundaries.py`
- `tests/support/member4_fake_apply_gateway.py`
- `tests/test_migrations.py`
- `tests/workflow_dependency_modeling/test_jaeger_address_policy.py`

### New source/test files (21)

- `alembic/versions/c9d2f7a41e86_add_after_to_applied_changes.py`
- `alembic/versions/d4e8b1f60a23_workflow_management_revisions.py`
- `app/api/workflow_management.py`
- `app/engine/failure_impact_prediction/deployment_impact.py`
- `app/engine/recommendation_evaluation_validation/application/changes.py`
- `app/engine/workflow_dependency_modeling/management.py`
- `app/shared/schemas/workflow_management.py`
- `app/static/css/pages/workflow-editor.css`
- `app/static/js/workflow-editor.js`
- `app/templates/components/workflow_editor.html`
- `extension/helper/internal/traceinstall/reviews_v2_adoption_test.go`
- `extension/ui-src/connect.test.mjs`
- `extension/ui-src/outstanding.js`
- `extension/ui-src/outstanding.test.mjs`
- `tests/docker_extension/conftest.py`
- `tests/docker_extension/test_disconnect_outstanding.py`
- `tests/failure_impact_prediction/test_deployment_impact.py`
- `tests/frontend/workflow-editor.test.mjs`
- `tests/recommendation_evaluation_validation/test_keep_and_ledger_api.py`
- `tests/recommendation_evaluation_validation/test_reset_reliability.py`
- `tests/workflow_dependency_modeling/test_workflow_management.py`
