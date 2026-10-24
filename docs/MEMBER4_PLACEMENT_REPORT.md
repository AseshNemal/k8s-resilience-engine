# Member 4: placement report (contract for the backend and UI builders)

Status: PLAN, nothing built. Plan of record: `PLANNING.md` ("Placement report") and `~/.claude/plans/agile-mixing-nautilus.md`.
This file is the shared contract. The backend branch (`feat/placement-backend`) implements it; the UI branch (`feat/placement-ui`)
builds against it with scripted fetches. **Do not change an API shape here without telling the other side.**

## Rules (from CLAUDE.md, apply to both sides)

- Member 4 only. Do not edit other members' folders (`app/engine/{workflow_dependency_modeling,deployment_topology_modeling,failure_impact_prediction}/`),
  `app/shared/schemas/`, or `app/main.py`.
- Real data only at runtime: no sample/demo/fixture data behind any route or page (`tests/test_no_sample_runtime.py`). Unknown stays `None`/`null`.
- Never mutate the original `DeploymentGraph`; use copies. Call Member 3 (`predict_portfolio_failure_impact`), do not reimplement its algorithm.
- Never claim experimental validation that was not run. Wording: "best validated so far". The words "optimal" and "experimentally validated" must not appear in
  any report text or UI string (a test scans for them).
- Errors: `EvidenceError` subclasses with UPPER_SNAKE codes (`app/shared/evidence_errors.py`); HTTP error body is `{"detail": {"code": "...", "message": "..."}}`.
- All text from the API is rendered with `textContent` in the UI, never as markup.

## Evidence levels (stored names; do NOT use L1/L2/L3, those already mean readiness/probe in `validation_loop/models.py`)

| Stored value | Words in the UI | Meaning |
|---|---|---|
| `PREDICTED` | Predicted | Model only (Member 3 on the candidate's simulated deployment). Not stored as a run; recomputed. |
| `PLACEMENT_OBSERVED` | Placement observed | Candidate applied temporarily on the current cluster, rollout settled, real placement observed, risk recomputed by Member 3 from the OBSERVED topology, then reverted. **No node is stopped.** |
| `FAILURE_TESTED` | Failure tested | Existing node-stop validation loop (Phase 2 wiring). |

`PLACEMENT_OBSERVED` never counts as proof for the Approve step. Only `/api/recommendations/validation-loop/proof` = `PROVEN` does.

## Score definitions

- Failure scenarios = every node of the basis (`includeControlPlane` default `true`, per the developer's choice; excluded nodes are listed with a reason).
- `matrix[workflowId][node]` = `true` (workflow survives that node failing), `false` (it does not), `null` (could not be computed).
- `score = sum_w normalizedWeight_w * (nodes where w survives) / (counted nodes)`; `null` if any cell is `null`. `risk = 1 - score`.
  `riskReduced = baseline.risk - candidate.risk`. Weights are normalised to sum 1; raw and normalised are both stored.
- `lowRisk.passed` = no workflow with weight>0 loses a mandatory service in any counted node failure AND `risk < baseline.risk` (threshold configurable).
  `passed` is `null` when the score is `null`.
- Repeated runs of one candidate: its score is the **worst** observed run. Runs are only comparable when `comparabilityKey` is equal.
- Weight sources, in order: `set-by-you` (request override), `declared-file`, `frozen-graph` (`weightMayBeDefault: true` when the value is 1.0), `equal-default`.

## Shared objects

```jsonc
// Scope: what a score is about. Same scope (same comparabilityKey) = comparable.
{
  "cluster": {"name": "docker-desktop", "uid": "…", "kind": "DOCKER_DESKTOP"},
  "namespace": "bookinfo",
  "workflows": [{"workflowId": "productpage-view", "source": "FROZEN",          // or "DECLARED"
                 "weight": 1.0, "normalizedWeight": 1.0, "weightSource": "equal-default", "weightMayBeDefault": false,
                 "services": ["productpage", "details", "reviews"]}],
  "weights": {"source": "equal-default", "digest": "…"},
  "basis": {"domain": "kubernetes.io/hostname", "includeControlPlane": true, "nodes": ["n1", "n2", "n3"],
            "excluded": [{"node": "n0", "reason": "not Ready"}], "digest": "…"},
  "scorerVersion": "all-single-node-weighted-v1",
  "comparabilityKey": "sha256:…"
}
// Result: one evaluation of a placement (baseline, predicted or observed)
{"matrix": {"productpage-view": {"n1": true, "n2": false, "n3": true}}, "score": 0.667, "risk": 0.333,
 "riskReduced": 0.333,            // absent on the baseline
 "lowRisk": {"passed": false, "reasons": ["productpage-view loses a mandatory service when n2 fails"]}}
```

## API (all under `/api/recommendations/placement`, plain JSON dicts, no shared schema)

Query/body fields shared by most routes: `namespace`, `workflowIds[]`, `workflowSource` (`FROZEN`|`DECLARED`), optional `weights` (`{workflowId: number}`),
optional `includeControlPlane` (bool). Mounted by `app/api/recommendations.py`; none of the second path segments may be `approval`, `request-approval`, `approve`,
`reject`, `application`, `apply`, `reset` (they would collide with the `/{recommendation_id}/…` routes).

### POST `/evaluate` (read-only, recomputed every time)
Response:
```jsonc
{"scope": {…Scope}, "baseline": {…Result},
 "candidates": [{"candidateKey": "sha256:…", "candidateIds": ["rec-productpage-view-reviews-topology_spread"], "type": "TOPOLOGY_SPREAD",
                 "services": ["reviews"], "label": "Topology spread on reviews", "yamlSha256": "…",
                 "predicted": {…Result incl. riskReduced},
                 "tieBreak": {"addedReplicas": 0, "deploymentsChanged": 1, "resourceCost": "LOW", "podsRecreated": 2},
                 "tiedWith": ["sha256:…"],
                 "runs": {"count": 2, "lastRunAt": "2026-10-24T10:00:00Z", "highestLevel": "PLACEMENT_OBSERVED"}}],
 "computedAt": "…", "notes": []}
```
`candidateKey` = hash of (type, sorted services, YAML sha256); one key may cover several recommendation ids. Only candidates with generated YAML are listed.

### POST `/runs/plan` (read-only)
Request: the shared fields + `candidateKey`, `yamlSha256`, `targetKind` (`DOCKER_DESKTOP`|`DISPOSABLE`).
Response: `{"planDigest": "…", "level": "PLACEMENT_OBSERVED", "candidate": {…row from /evaluate}, "prediction": {…Result}, "baselinePlacementDigest": "…",
"issues": [{"code": "APPLICATION_ACTIVE", "message": "…", "blocking": true}], "canApprove": true,
"acknowledgement": {"required": true, "text": "Stop nothing; patch the Deployments of <namespace> temporarily and revert them (two rollouts)."},
"target": {"kind": "DOCKER_DESKTOP", "name": "docker-desktop", "uid": "…"}, "notes": []}`

### POST `/runs` (starts one run; needs `RESILIENCE_ALLOW_CLUSTER_WRITES=1`)
Request: `{"plan": {…the /runs/plan request body…}, "planDigest": "…", "desktopAcknowledged": true}` → `202 {"placementRunId": "…", "runKey": "…", "status": "RUNNING"}`.
409 codes: `PLAN_CHANGED`, `RUN_BLOCKED`, `APPLICATION_ACTIVE`, `APPLIED_CHANGE_ACTIVE`, `WRITES_NOT_ARMED`. One confirmation per run; the digest is recomputed on approval.

### GET `/runs/{placementRunId}` (full stored record; poll every ~2 s while `status == "RUNNING"`)
```jsonc
{"placementRunId": "…", "runKey": "…", "candidateKey": "…", "candidateId": "…", "candidateType": "TOPOLOGY_SPREAD", "services": ["reviews"],
 "label": "Topology spread on reviews", "yamlSha256": "…", "yamlText": "…",
 "level": "PLACEMENT_OBSERVED",
 "status": "RUNNING",   // PLANNED | RUNNING | COMPLETED | INCONCLUSIVE | FAILED | INTERRUPTED | NEEDS_MANUAL_RESTORE
 "stage": "OBSERVE",    // APPLY | VERIFY_ROLLOUT | OBSERVE | RESTORE | OBSERVE_AFTER_REVERT | DONE
 "preliminary": true, "evidenceClass": "REAL_CLUSTER",   // or "TEST_DOUBLE"
 "scope": {…Scope}, "baseline": {…Result}, "predicted": {…Result},
 "observed": {…Result, "placementComparison": [{"service": "reviews", "predictedNodes": ["n1","n2"], "observedNodes": ["n1","n2"], "matches": true}]} /* null until observed */,
 "afterRevert": {"baselinePlacementRestored": true, "flags": []},   // flag "BASELINE_PLACEMENT_CHANGED" when false
 "reasons": [], "events": [{"at": "…", "event": "APPLY_STARTED", "detail": null}],
 "evidenceSources": {"workflow": "frozen-graph", "topology": "live-discovery", "impact": "member-3"},
 "createdAt": "…", "startedAt": "…", "finishedAt": null}
```
An unsettled rollout gives `status: "INCONCLUSIVE"`, reason `ROLLOUT_NOT_SETTLED`, and no observed score.

### GET `/runs?namespace=&comparabilityKey=&candidateKey=&limit=` → `{"runs": [summary]}`
summary = `{placementRunId, candidateKey, label, level, status, preliminary, observedScore, observedRisk, riskReduced, lowRisk, createdAt, finishedAt, comparabilityKey}`; `limit` ≤ 100.

### GET `/tested?<shared fields>` (feeds the right-hand panel)
`{"scope": {…}, "rows": [{"candidateKey": "…", "label": "…", "highestLevel": "PLACEMENT_OBSERVED", "observedScoreWorst": 0.667, "observedRiskWorst": 0.333,
"runs": 3, "agreement": "2/3", "lastRunAt": "…", "preliminary": true, "comparable": true}], "excluded": [{"placementRunId": "…", "reason": "different weights"}]}`
`agreement` = runs whose observed placement matched the prediction / completed runs.

### GET `/compare?ids=a,b,c` (2 to 3 runs)
`{"comparabilityKey": "…", "baseline": {…Result}, "columns": [{"placementRunId": "…", "label": "…", "level": "…", "preliminary": true, "score": 0.667, "risk": 0.333, "riskReduced": 0.333, "lowRisk": {…}, "matrix": {…}}]}`; `409 NOT_COMPARABLE` when keys differ.

### GET `/report?<shared fields>`
```jsonc
{"reportVersion": 1, "generatedAt": "…", "scope": {…}, "baseline": {…Result},
 "best": {"candidateKey": "…", "label": "…", "evidenceLevel": "PLACEMENT_OBSERVED", "preliminary": true, "runs": 3, "agreement": "2/3",
          "observedRiskWorst": 0.333, "riskReduced": 0.333, "lowRisk": {…}, "matrix": {…}, "predictedVsObserved": [{…placementComparison…}],
          "yaml": "…", "yamlSha256": "…", "tiedWith": []},      // or null
 "bestReason": null,                                              // text when best is null, e.g. "No candidate has a completed placement check yet"
 "candidates": [{"candidateKey": "…", "label": "…", "highestLevel": "PREDICTED", "validated": false, "predicted": {…}, "observedRiskWorst": null}],
 "notTested": {"candidates": ["…"], "failures": ["No node was stopped"], "domains": ["zone failures", "the shared Docker Desktop VM"], "probe": "no HTTP workflow check"},
 "honesty": {"evidenceClass": "REAL_CLUSTER", "statements": ["Best validated so far, not proven optimal.", "…"]},
 "excludedRuns": [{"placementRunId": "…", "reason": "different weights"}]}
```
"best" rule: candidates with at least one COMPLETED run under this scope; rank by highest evidence level, then `lowRisk.passed`, then worst observed risk, then agreement, then `tieBreak`. Ties are listed in `tiedWith`, never broken silently.

### POST `/runs/{placementRunId}/abort` → `{"run": {…full record…}}`

## Stored record: table `placement_runs` (additive Alembic migration after `a3c6e1f90b52`; model in `app/shared/persistence/models.py`)
Columns follow the `GET /runs/{id}` shape (JSON columns for `workflows`, `weights`, `failure_basis`, topologies, matrices, `placement_comparison`, `low_risk`, `revert`,
`evidence_sources`, `reasons`, `events`, `thresholds`; text for `yaml_text`; indexed: `placement_run_id` unique, `run_key`, `comparability_key`, `candidate_key`,
`(cluster_uid, namespace)`). Each run also gets a `validation_loop_runs` row with `mode='OBSERVE'` (`workers=()`, `max_failures=0`) so blocking, Apply/Reset exclusion,
the single worker thread, restore points and restart recovery are reused; those rows are filtered out of `history()` and `proof_from_history` **in the SQL query**.

## UI rules (UI branch)

- Keep the five steps. Add a right-hand rail `<aside data-role="rec-placement-rail">` (>= 1100 px: grid column; below: a `<details>` under the current step). Rows come from `/tested`.
- Chips use the "Words in the UI" column above. Show "Preliminary" on quick evidence. Compare allows up to 3 comparable rows (others disabled with the reason).
- "Open placement report": slide-over from `/report`, with **Export JSON** (Blob download, pattern at `app/static/js/components/workflows.js` `downloadJsonArtifact`) and **Download YAML**.
- Step 3 gains the predicted all-node risk and a "Test placement" button per candidate. Step 4 gets a segmented control "Placement check (no node stopped)" / "Node-stop test"
  (the existing proof panel). A placement check shows: candidate, what will happen (acknowledgement text), issues, ONE confirmation, progress through
  `APPLY → VERIFY_ROLLOUT → OBSERVE → RESTORE → OBSERVE_AFTER_REVERT`, then predicted vs observed.
- Stepper: `computeSteps` gets `proof: "observed"` → step 4 state `partial` (mark `◐`, caption "Placement observed, no node stopped"); never "done"; step 5 unchanged.
  `proofNoteText` must not read an observe-only history as "not proven / failed".
- Simple view: one card "Best validated so far: <label> · Placement observed · <n> runs" + "Open report". Research view: the full rail, compare, per-workflow × per-node matrices.
- Docker Desktop tokens (`--dd-*`), sentence case, no all-caps labels; follow `recommendation-proof.js` (`createXPanel({root, document, fetchImpl, schedule, getContext})`,
  sequenced responses, backoff polling, `textContent` only). Tests: `node --test` with `tests/frontend/support/mini-dom.mjs` and scripted fetch.

## File ownership (no file is edited by both branches)

| Backend branch `feat/placement-backend` | UI branch `feat/placement-ui` |
|---|---|
| `app/engine/recommendation_evaluation_validation/placement/**` (new) | `app/static/js/recommendation-placement.js` (new) |
| `…/validation/kubernetes_executor.py`, `…/validation_loop/**` | `app/static/js/recommendation-render.js`, `app/static/js/components/recommendations.js` |
| `app/api/validation_loop_placement.py` (new), `app/api/validation_loop_environment.py`, `app/api/recommendations.py` (mount only) | `app/static/css/pages/recommendations.css`, `app/templates/components/recommendations.html` |
| `app/shared/persistence/models.py`, `alembic/versions/**`, `tests/test_migrations.py` | `tests/frontend/**` |
| `tests/member4_placement/**`, `tests/support/**`, `tests/member4_validation_loop/**` | |

`CLAUDE.md`, `AGENTS.md`, `PLANNING.md` and this file are edited by Claude only.

## Verification

Backend: `python -m pytest tests/member4_placement tests/test_migrations.py tests/test_no_sample_runtime.py tests/recommendation_evaluation_validation tests/member4_validation_loop -q`, then the whole suite.
UI: `node --test tests/frontend/*.mjs` and `node --test extension/ui-src/*.test.mjs`. Nothing here has run on a real cluster.
