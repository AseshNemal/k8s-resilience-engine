# Final verification checklist — recovery update 2026-11-16

## Source snapshot addendum

The user subsequently authorized a source-only commit including this checklist and `docs/AGENT_HANDOFF.md`. Historical statements about the uncommitted checkout below refer to recovery, not the final commit state. Generated verification artifacts, databases, credentials and kubeconfigs are excluded.

- [x] Final review fixed Reset forwarding a browser click event into the `external` policy; the existing event-bearing regression check passed with all **32** tests in `node --test tests/frontend/recommendation-apply-test.test.mjs`.
- [x] Read-only extension status reported **0.1.678**, source `7f019d0`. Update dry-run with `--keep-all` reported nothing changed. No build/install/update was executed.
- [ ] Trace-service/workload mapping and unrelated-traffic attribution gaps resolved: **outstanding**. Acceptance preparation was redirected to committing the current implementation.
- [ ] Persistent-data backup and migration rehearsal/rollback plan completed: **outstanding**. The extension entrypoint automatically runs Alembic; do not perform the actual update before a verified backup and approved recovery procedure.

No deployment, persistent migration, cluster write or fault injection is authorized by this source snapshot.

This checklist separates isolated source verification from real-cluster acceptance. Previous historical checklist is retained at `tmp/previous-verification-checklist.md`; its real-cluster observations do not prove this working tree.

## Recovered repository and boundaries

- [x] Read AGENTS.md, CLAUDE.md, PROJECT_CONTEXT and both existing handoff documents; inspected actual diffs and new files.
- [x] Branch remains `feat/bookinfo-tracing-on-main`; HEAD remains `7f019d0`. Prior modifications preserved.
- [x] No commit/push/merge/rebase, extension install/update, real-cluster request, Kubernetes write or failure injection during recovery.
- [x] Explicit user authorization for cross-component work reflected in the exact-file ownership-test exception list; public-service architecture assertion retained (`test_member_boundaries.py`, focused retest 2 passed).

## Automated verification actually executed

- [x] Consolidated Python: 3634 passed, 19 skipped, one obsolete ownership guard failed (`tmp/final-python-verification.log`).
- [x] Corrected ownership guard: 2 passed. Final affected Python checks: 148 passed (`tmp/final-focused-verification.log`).
- [ ] A completely green full Python rerun after the final refinements: not run; the consolidated failure and the focused retests are recorded separately.
- [x] Combined frontend and extension Node tests: 793 passed (`tmp/final-node-verification.log`). Final focused editor/context/renderer retest: 71 passed (`tmp/final-focused-node.log`).
- [x] All 17 Go helper packages passed (`tmp/final-go-verification.log`).
- [x] New script syntax, new backend-module compilation and `git diff --check` passed.
- [x] Fresh/repeated database migrations, no-sample runtime guards and kubeconfig-isolation checks passed within the consolidated run.
- [ ] Installed extension database migrations and preservation of its actual existing rows: not run; developer approval required for target update.

## Behavior verified against doubles

- [x] Per-node deployment-only service outcomes and rankings, no synthetic workflows; incomplete evidence blocked (`test_deployment_impact.py`, `test_live_context.py`).
- [x] Backend findings survive the context adapter, render unknown workflow survival/blast radius and offer no workflow approval (`recommendation-context.test.mjs`, `recommendation-render.test.mjs`).
- [x] Current/proposed owned-field comparison before approval (`test_independent_inputs.py`, `recommendation-render.test.mjs`).
- [x] Reset read-back snapshots, outside-change refusal, partial reset, Keep, same/other/unknown cluster ledger (`test_reset_reliability.py`, `test_keep_and_ledger_api.py`).
- [x] Version-pinned gateway restore and validation recovery using current resource versions, preserving records on refusal (`test_validation_gateway.py`, `test_kubernetes_executor.py`).
- [x] Three-way disconnect, reset-wait, unreadable-ledger refusal and reconnect display (`test_disconnect_outstanding.py`, `connect.test.mjs`, `outstanding.test.mjs`).
- [x] Apply-and-test partial reset and Reset/Keep choices (`recommendation-apply-test.test.mjs`, `recommendation-operation.test.mjs`).
- [x] Existing reviews-v2 adoption without Deployment ownership/deletion, with rollback and Undo (Go `bookinfotrace`, `traceinstall/reviews_v2_adoption_test.go`).
- [x] Manual graph without traces; immutable history, stale-save refusal, tombstone deletion, cluster/namespace/service checks (`test_workflow_management.py`).
- [x] Unknown labels and unreachable nodes cannot enter reviewed export; experimental-proof fields cannot be client-supplied (same file).
- [x] Fixed recording end, trace deduplication, span/trace provenance, refresh revision and observed-call/declared-label separation (same file).
- [x] Undo/redo, preserving manual labels on merge, rejecting implicit name mapping (`workflow-editor.test.mjs`).

## Browser checks (isolated fake cluster only)

- [x] Studio renders live-to-the-double logical services; add, connect, classify, name, set entry/criticality, save reviewed, reload/reopen.
- [x] Reviewed graph appears in Recommendations as declared research evidence; no-workflow node findings have explicit unknown workflow metrics and no Apply button.
- [x] Saving a later draft creates version 2 and retains version 1 in history.
- [x] Desktop canvas and mobile stacked layout inspected; 390px settled page did not overflow viewport.
- [ ] Browser Start/Stop against a real instrumented application: not verified. Recording backend and merge logic have isolated tests only.
- [ ] All controls tested in Docker Desktop's actual embedded browser, including host-assisted disconnect dialogs: not verified.

Evidence: `tmp/workflow-editor-{initial,reviewed,mobile}.png`; browser snapshots under `tmp/editor-browser-artifacts/`. The temporary acceptance server used only an isolated SQLite database and fake Kubernetes source and was stopped afterwards.

## Mandatory controlled acceptance still outstanding

Every write/install/fault step below requires explicit developer approval. Do not tick with a model result or synthetic test.

1. [ ] Run target database migrations through `d4e8b1f60a23`; inspect legacy applied_changes rows and recovery data.
2. [ ] Approve and install/update the extension from this working tree. Record image version and source identity.
3. [ ] Connect the intended cluster; verify cluster UID/access and compare Service/Deployment/Pod/Node discovery with Kubernetes observations.
4. [ ] With no workflows/traces, confirm deployment-only node findings and unknown workflow metrics.
5. [ ] Manually review a workflow for a generic non-Bookinfo application; inspect declared-tier recommendations, feasibility, ranking and exact proposed fields.
6. [ ] Use an instrumented application during Start/Stop Recording; inspect actual trace/span references, coverage, namespace attribution limits and manual correction. No mandatory label may be described as experimentally proven from trace frequency or user selection.
7. [ ] Run the existing controlled dependency assessment/freeze path where production frozen evidence is required.
8. [ ] Named approval and Apply: verify target UID, accepted patch, rollout completion and model recalculation from fresh placement. Approval/status alone must not patch.
9. [ ] Observe current/proposed fields including API-server defaults and admission effects; compare actual pod placement.
10. [ ] Reset; verify fields restored and rollout observed. Race a concurrent resourceVersion/spec edit and confirm refusal/retained recovery records.
11. [ ] Make an external field change; normal Reset must refuse; partial Reset must preserve the outside field.
12. [ ] Keep must not patch; disconnect must ask Reset/Keep/Cancel, block during reset, preserve ledger on reconnect and offer no reset on a different cluster.
13. [ ] Optional Bookinfo tracing Plan/Apply/Undo preserves existing reviews-v2 UID and replica count; generic analysis must work without Bookinfo.
14. [ ] Separately authorized controlled failure validation: assess resources, designate cluster, review plan, approve bounded node stops, measure outcomes, restore workers/revert Deployments and retain predicted versus observed results separately.
15. [ ] Check Windows/Linux helper behavior and Docker Desktop rendering where required.

## Known unsupported or limited behavior

- New recording UI reads Jaeger only; it does not install instrumentation or propagate a recording tag.
- Time/name correlation does not attest trace cluster/namespace or isolate concurrent traffic. Unmatched names are listed, never guessed. Bounded query coverage is not a complete ingestion snapshot.
- Supporting/Optional project to optionalServices in the shared graph; conditional dependency semantics and automatic trace-name mapping are unsupported.
- Reviewed manual/hybrid graphs are declared research evidence; they do not bypass assessment/freeze or production approval.
- Deleted documents keep API-readable history, but the active selector hides deleted items.
- Legacy applied changes with no after-snapshot cannot identify outside drift. Recovery spec-generation safeguards can require manual reconciliation after uncertain writes or outside edits.

Read `docs/AGENT_HANDOFF.md` for the full changed-file inventory, test commands and current limitations. Readiness: prepared for supervised real-cluster acceptance, not empirically validated and not a production-readiness claim.


## Workflow Manager redesign follow-up — 2026-11-16

- [x] Manager, guided recording, shared canvas editor, review/save confirmation and risk-analysis navigation implemented without backend changes.
- [x] Advanced infrastructure, evidence/history, reviewer/criticality and existing research tools remain accessible through progressive disclosure.
- [x] Incoming-edge service classification never invents calls; new/imported calls remain Unknown; observations never become experimental validation.
- [x] Targeted editor model tests: 5 passed. Existing management integration and extension rendering tests: 91 passed. Syntax and diff whitespace checks passed. Full suite not rerun.
- [x] Isolated browser: manual graph/save, native service drop and connection drag, node move/undo/redo, synthetic Start/Stop/import/save with trace provenance, history and manager rename/duplicate/delete.
- [x] Browser layouts inspected at 1440, 1024 and 390px; mobile had no horizontal overflow. Docker CSS dark theme inspected locally; final browser console had no errors.
- [ ] Updated UI tested inside the installed Docker Desktop extension: not performed; update remains separately approved work.
- [ ] Real application Start/Stop, exact trace-to-workload mapping and unrelated-traffic isolation: still outstanding. Synthetic browser checks do not satisfy this acceptance step.

The isolated browser servers were stopped; generated logs/screenshots/harnesses stay under ignored `tmp/`. This follow-up does not change the outstanding migration, deployment or real-cluster approval boundaries above.


## Additive methods and application-test evidence — 2026-11-16

- [x] One editor supports manual additions, recorded activity and test evidence; merges preserve graph metadata, positions and labels, with undo/redo and the existing version history.
- [x] Test-case identity/target/timestamps/request trace IDs, exact-ID fetch or normalized Jaeger import, existing call extraction and success-condition evaluation integrated through the owning service.
- [x] Imported and recorded calls stay Unknown by default; conflicting declarations need visual review. Server-attested validated classifications/references cannot be overwritten or removed here.
- [x] Evidence references pin source revision and candidate. A selected test/operation does not pool unrelated source candidates. Legacy recording references are retained explicitly.
- [x] Trace refresh never reruns tests, preserves historical call references, and leaves missing/current outcomes indeterminate.
- [x] Python and Node export helpers produce metadata accepted by the backend contract; the Playwright interceptor was checked against a context double. Application tests still require integration in their own repository.
- [x] Isolated browser: manual Optional + conflicting test Required, blocked review, explicit decision, draft v1, recorded merge, reviewed v2, source provenance and history. Mobile/desktop layout checked; 390px had no horizontal overflow.
- [ ] Actual external Pytest/Playwright suite, real Jaeger ingestion and customer instrumentation: not run.
- [ ] New native HTTP probe flow against the real application and installed extension: not run.
- [ ] Ordinary recording's namespace/cluster and concurrent-traffic attribution: existing limitations remain; no new guarantee.

The shared WorkflowGraph and existing research algorithms are reused. Test artifacts use the existing revision table; no migration was added or applied. All development changes remain uncommitted; deployment and cluster operations still require separate explicit approval.

Final additive-workflow verification: **102 passed** in the focused management/test-evidence/extension-renderer Python checks; **7 passed** editor Node checks; exporter contract/interception smoke checks, JavaScript syntax and `git diff --check` passed. No full-suite rerun. Concurrent recommendation-engine edits are outside these checks.


## User-authorized combined source commit — 2026-11-16

The user subsequently requested “commit all”. This source snapshot includes the visual/additive workflow identification work and the concurrent recommendation audit fixes, including API-server version evidence. Earlier “uncommitted” statements above describe implementation-time state. Final commit preparation rechecked all staged/unstaged/untracked source, both retained handoff files, the two intended unused-strategy deletions, and credential/generated-file patterns; local databases, kubeconfigs, credentials and ignored scratch artifacts are excluded.

New focused commit checks: **98 Python tests passed** across recommendation audit/rollout/combinations/YAML/offline/boundary checks, and **84 Node tests passed** for recommendation operation/rendering. Earlier workflow identification checks remain **102 Python / 7 Node passed**, plus isolated browser and exporter checks. No full-suite rerun. No extension update, persistent migration, connected-cluster modification or fault injection was performed. Installed-extension and real-cluster verification remain pending separate approval.
