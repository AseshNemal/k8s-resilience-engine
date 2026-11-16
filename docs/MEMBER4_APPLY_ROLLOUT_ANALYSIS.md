# Member 4: Apply, rollout, post-change analysis and Reset as one operation

Owner: Member 4. Built 2026-10-23 on top of the Apply/Reset step in `docs/MEMBER4_INPUT_SOURCES.md` section 6. Nothing here changes another member's component, a shared
schema (`app/shared/schemas/`) or `app/main.py`. The journey it completes:

```
Review → Approved → Applying → Rollout → Analysis → Results   (and the same six for a Reset)
```

## Candidate flow across all workflows (2026-10-24)

The Recommendations page evaluates all workflows for the selected application together. There is no workflow dropdown or
part-by-part hand-off: select a candidate, read its changes and why it helps, approve it, then explicitly Apply it. A combination
is one candidate with one generated YAML proposal and one Apply/Reset operation covering its Deployments.

The existing detail and request-approval routes accept `all_workflows=true`. Frozen evidence uses the production approval tier;
declared evidence uses the separate research tier with a named acknowledgement. These sources are never mixed. Approval and Apply
recompute the same workflow set from stored server provenance. The proposal fingerprint includes a digest of every workflow
definition and freeze identity; declared approvals also retain the declaration file digest. Missing workflow evidence blocks the
aggregate approval with `PORTFOLIO_EVIDENCE_INCOMPLETE`. Eligibility, justification for an alternate candidate, YAML regeneration,
write arming, target checks, snapshots and explicit Apply/Reset remain in force.

After rollout, the aggregate failure impact is recalculated over fresh observed placement using Member 3's evaluator for each
workflow, with the existing criticality weighting. Changed definitions make the comparison unavailable. This is still a model
recalculation, not a fault-injection experiment. The single-workflow proof panel is not offered for an aggregate change; the page
labels it as a prediction without test-cluster proof. The earlier analysis-only `/portfolio/detail` endpoint remains read-only.

## What it is, and what it is not

An approved change is applied to the connected research cluster on a named person's click, the **rollout** is watched from the Deployments' own status, and when it
has completed the workflow's failure impact is **recalculated from the freshly observed topology** and compared with the numbers fixed before the change.

> **Post-change risk recalculated from live topology.** It is a model recalculation over observed pod placement, not an experimentally observed workflow failure rate and
> not a controlled-experiment validation. (`analysis.RESULT_LABEL`, stored on every analysis row and returned in every response.)

Four different questions, answered separately and never inferred from one another (`states` in the status payload):

| Question | Answered by | Never implied by |
| --- | --- | --- |
| Were the patches accepted? | the stored change (`APPLIED`, `RESET`, …) | — |
| Has the rollout completed? | the Deployments' generation and replica counts (`rollout`) | patch acceptance, the stored status, old ready pods |
| Has the post-change analysis completed? | the analysis row (`COMPLETED`, `FAILED`, `UNAVAILABLE`, `PENDING`) | a completed rollout |
| Is it experimentally validated? | the validation subsystem's measurements (`experiments`), shown apart | any of the above; `experimentallyValidated` is always `false` here |

Nothing in Apply, Reset or the analysis stops a node, deletes a pod or injects a failure.

## Execution model: persisted state and bounded polling

There is no background job. Apply patches synchronously (seconds), persists, and returns. Everything after that is state in the database plus a status request that advances it:

* `GET …/{id}/application` reads the touched Deployments once, stores where the rollout stands (`applied_changes.rollout`), and, if the rollout has just completed, runs the
  analysis. It reads the cluster and writes this service's own tables; it never patches. Only state *transitions* become events, never one event per poll.
* The page polls at the interval the server states (`operation.pollMs`, 3 s while something is in progress, none once settled), one request at a time, backing off on errors,
  for at most 15 minutes, after which **Check again** is offered. Timers are cancelled on page hide and on a cluster change.
* A page refresh or a backend restart resumes the same record (`GET /api/recommendations/applications/current` finds the latest application whichever recommendation is
  selected). Nothing writes except an explicit click.
* One Apply or Reset at a time (a process lock around the checks and the writes, and the persisted `ACTIVE` check across restarts). A second click or a second tab gets
  `APPLICATION_ACTIVE`. While a Reset is still rolling out, a new Apply is refused (`RESET_ROLLOUT_IN_PROGRESS`).
* No database transaction is held open while waiting: each poll is its own short transaction. Targeted writes (`advance_rollout`, compare-and-set on the rollout's start time;
  `append_events`) mean a poll can never write a stale copy over a concurrent Apply or Reset.
* A request that times out is **not** taken to have failed: the page reads the server's record before re-enabling any control (`reconcile`), and says what the record shows.
* A backend that died part-way through an Apply leaves the row `APPLYING`; with no apply running in this process the status says **interrupted** and offers Reset, the stored
  state being the way back.

## Rollout states (`application/rollout.py`)

| State | Meaning |
| --- | --- |
| `rolling-out` | The controller has not observed the latest generation, or updated / total / ready / available replicas have not all reached the desired count. Pods the scheduler reports `Unschedulable` set `blocked` and say why, while the rollout is still waiting |
| `completed` | Every touched Deployment is settled on its latest generation (`observedGeneration ≥ generation`, `updated = total = ready = available = desired`). **Old ready pods never count** |
| `failed` | A touched Deployment is gone, was deleted and recreated (another UID), or its controller reports `Progressing=False` (e.g. `ProgressDeadlineExceeded`) |
| `timed-out` | 10 minutes after the write with none of the above. Re-checked on the next request, so a late recovery is seen |
| `unknown` | A change recorded before rollout tracking existed. Reported unknown, never assumed complete |
| `connection-lost` | Response-only: this read failed (unreachable, credential rejected, not allowed); the stored state is left as it was, and the reason is said |

## The comparison (`application/analysis.py`, `application/live_analyser.py`)

* **BEFORE** is the blast radius (and failed services / workflows, placement and replica counts of the changed services) of the topology the recommendation was generated from.
  **PREDICTED** is the approved proposal's what-if result. Both are written to `applied_change_analyses` when the change is applied and **never updated**; later candidate
  generation cannot replace them. For a Reset the "predicted" side is the original baseline, labelled a *restoration target*, not a prediction.
* **AFTER** is Component 3's `predict_failure_impact` over a topology read **after** the rollout completed (`capturedAfterRollout` is checked and stored), for the same workflow,
  the same namespace and the same assessed node. The comparison basis (workflow id and its freeze id or declaration digest, namespace, node, metric, target services, cluster)
  is stored with the baseline.
* Reduction is in **percentage points** of blast radius (100% → 20% is 80 points). A relative reduction is a separate, labelled figure and is *not defined* for a zero baseline.
  A value that is unknown stays `null` and is drawn "unknown", never zero.
* **The comparison is refused, with the reason, rather than drawn misleadingly** (`UNAVAILABLE`, terminal): the connected cluster is not the one the change was applied to
  (`CLUSTER_CHANGED`), the frozen workflow was frozen again or the declared workflow edited (`WORKFLOW_CHANGED`), the assessed node is no longer in the topology
  (`ASSESSED_NODE_UNAVAILABLE`), the change was reset before it was analysed (`SUPERSEDED_BY_RESET`), the apply failed (`APPLY_FAILED`).
* A transient failure (Component 2 or 3 cannot be read now) is `FAILED` and leaves the applied change, its rollout and **Reset** exactly as they were. `POST …/analysis/retry`
  runs it again; it applies and resets nothing, and only a failed analysis can be retried.
* A Reset verifies **restoration** when its rollout completes: replica count, spread constraints and anti-affinity compared with the stored snapshot, and the Deployment UIDs. A
  difference (someone scaled it since) is listed and the reset is not called restored. Other fields of the Deployments are never touched, so unrelated changes survive.

## Adapter provenance and replacement points

| Adapter (Member 4) | Real evidence it reads | Owner's service it calls | Replace when |
| --- | --- | --- | --- |
| `ProductionAnalyser` | Component 1's frozen workflow; Component 2's live topology (through Component 3's `get_runtime_component_inputs`) | `predict_failure_impact` (Component 3) | Nothing to replace: it is already only calls. It needs a frozen workflow for the application |
| `ResearchAnalyser` | Member 4's declaration file (a **user declaration**, labelled so); Component 2's `discover_live` | `predict_failure_impact` (Component 3) | When Component 1 freezes a workflow for the application: the production tier then applies and this tier is retired |
| `KubernetesTestClusterGateway.read_rollout` (`app/integrations/kubernetes/`) | Deployments and pods of the namespace (read only): generation, replica counts, UID, `Progressing`, `PodScheduled=False/Unschedulable` | — | It is integration code; it stays |

Neither adapter holds a fixture, a demo graph, a hard-coded placement or an invented capacity. If the cluster, the workflow or the topology cannot be read, the analysis row says
which (`FAILED` with the owner's code, e.g. `DISCOVERY_FAILED`, `TOPOLOGY_INCOMPLETE`, `WORKFLOW_EVIDENCE_UNAVAILABLE`).

## What each displayed fact is

Review shows each fact with its kind: **Live observation** (cluster, namespace, target Deployments, feasibility checked against live facts), **User declaration** (a declared
workflow and who declared it), **Model assumption** (the assessed failed node, resource-cost classes), **Prediction** (strategy, proposed change, expected benefit, the
Predicted card) and **Missing evidence** (anything not supplied: it is never defaulted). The After card is Component 3's model run over a **live observation** of placement.
**Controlled-experiment observations** exist only in the validation subsystem, and only completed real-cluster measurements for the recommendation are listed, in their own section.

## Routes (both tiers; the declared workflow's are under `/declared/`)

| Route | Purpose |
| --- | --- |
| `GET /api/recommendations/{id}/application` | Status: `application`, `rollout`, `analysis{apply,reset}`, `operation{stage,steps,headline,nextAction,pollMs,connection,interrupted}`, `states`, `experiments`, `canApply`, `canReset`, `canRetryAnalysis`. Advances the stored rollout and analysis; patches nothing |
| `POST /api/recommendations/{id}/apply` `{appliedBy}` | Apply an APPROVED change (all earlier checks unchanged; the baseline is planned and stored before the first write) |
| `POST /api/recommendations/{id}/reset` `{resetBy}` | Restore the stored fields on the same cluster and the same Deployments (UID checked); starts the reset's own rollout and analysis |
| `POST /api/recommendations/{id}/analysis/retry` | Run a failed recalculation again. Applies and resets nothing |
| `GET /api/recommendations/applications/current` | The latest application of either tier, with its status; how a reloaded page finds it again. Reads and records progress only |
| `GET /api/recommendations/applications?limit=` | The operation history as stored (no cluster read) |

Persistence (additive migration `f4b91c2d7e03`): `applied_changes.rollout` (nullable JSON) and the new `applied_change_analyses` table (`basis`, `baseline`, `predicted`
written once; `observed`, `comparison`, `error` written when the analysis ends). Recovery snapshots (`applied_changes.before`) and the event history are never removed.

## The page

The **Applied change** panel sits outside the page region that re-renders when the selection changes, and is keyed by the stored application id, so it survives a different
selection, a recommendation that is no longer generated, an engine that now produces no candidates, and a reload. It shows the six-stage indicator (text as well as colour,
`aria-current`), a polite live headline, an indeterminate bar while waiting (there is no completion percentage to show), the server-recorded steps, the Before / Predicted /
After cards, a bar graphic of the returned values, the reduction in points, the pod placement of the changed services before / predicted / after, and expandable technical
details (identities, hashes, timestamps, per-Deployment rollout evidence, events, assumptions and unavailable evidence, the applied YAML and the raw status). Controls are
disabled the moment a request leaves; "Applying change…" and "Resetting…" are on screen before the answer. All text is set with `textContent`.

Tests: `tests/recommendation_evaluation_validation/test_apply_rollout_analysis.py` (lifecycle, comparison, failure modes, concurrency, restart), `test_application_api.py`,
`test_member_boundaries.py`; `tests/frontend/recommendation-operation.test.mjs` runs the panel against the real template markup in a tree-faithful DOM
(`tests/frontend/support/mini-dom.mjs`).

## Limits

* Pods already running are not moved by a patch: the Deployment rolls out and the scheduler places replacements. The AFTER topology is therefore only as settled as the rollout.
* Rollout cost (pods restarted while the change rolls out) is not modelled.
* The blast radius of one workflow is 0% or 100%; "percentage points" become informative with several workflows (Component 3's portfolio) or a different metric.
* The process lock and the in-process analysis guard assume one backend process, which is how the extension runs it.
* **Not yet run against a real cluster by an automated test.** The live verification and its result are recorded in the completion report of the session that built this, not here.

## Keep, and what happens next (2026-11-17)

**Keep** writes nothing to the cluster (`ApplicationService.keep`): the change stays in the ledger as `KEPT`, stops blocking another change, and can
still be reset while the cluster holds what it set. Keep is refused unless the rollout is verified complete. After a successful Keep the page re-reads
the cluster (`controller.reload()` through `onReadyForNext`), so the risks and the ranked changes are recalculated from the cluster as it is now, and
the action dock offers "Choose another change" next to "Reset change". A second change is still refused (`APPLICATION_ACTIVE`) while one is applied
and undecided.
