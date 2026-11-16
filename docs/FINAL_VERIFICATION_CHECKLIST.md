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
