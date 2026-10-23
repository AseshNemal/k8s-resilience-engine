# Recommendations page: how it gets its evidence, and what is still missing

Owner: Member 4. The page works from the **currently connected cluster** and the **approved frozen workflows**. A person
does not type a namespace, a workflow ID or a node: the page asks the backend what is available, uses the only choice
automatically, offers a dropdown when there are several, and says exactly which evidence is missing when it cannot go on.
No shared contract or schema is changed, and Components 1, 2 and 3 are only called, never modified.

## What the person sees

1. The page reads the connected cluster's name (inside the extension) and asks `GET /api/recommendations/context`.
2. **Application.** If exactly one namespace holds applications it is used. With several, a dropdown asks; none is guessed.
3. **Workflow.** Every approved *and frozen* workflow is listed, with whether it applies to the application's services. The
   only applicable one is used automatically; with several, a dropdown asks. One that names services that are not in the
   application, or whose placement is not fully resolved, is listed but cannot be chosen, with the reason.
4. **Evidence checklist.** Cluster, application, deployment topology (Component 2), approved frozen workflow (Component 1) and
   failure impact (Component 3), each `available`, `incomplete`, `missing` or `pending`, with the exact next action.
5. When everything is available the page asks `GET /api/recommendations/live/detail` and shows, per candidate: the root
   cause, the recommended change, feasibility and why, the current placement, predicted blast radius before and after, the
   resource and configuration cost, the ranking and why it was or was not selected, and **whether it is ready for approval**
   (Member 4's own eligibility rule, the one the approval gate enforces).
6. An optional "failure to protect against" dropdown (the cluster's own nodes) defaults to the node whose loss hurts most.

## Read-only assessment endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/recommendations/context?namespace=&workflow_id=` | The evidence checklist and the choices. Always answers; a missing piece of evidence is the answer, not an error |
| `GET /api/recommendations/live/detail?workflow_id=&namespace=&failed_node=` | The explained recommendation. Same inputs and coded errors as `/live` |
| `GET /api/recommendations/live` | Unchanged: the bare `RecommendationReport` |
| `GET /api/recommendations/live/failures?workflow_id=&namespace=` | Each worker node assessed separately for a frozen workflow, worst blast radius first |
| `GET /api/recommendations/declared/failures?workflow_id=&namespace=` | Same overview for a declared workflow, labelled declared and outside production approval |

## Where each input comes from

| Evidence | Producer (public interface only) |
| --- | --- |
| Connected cluster, applications, services, how complete each namespace's topology is | Component 2: `discover_all` |
| Approved, frozen workflow graphs | Component 1: `list_dependency_assessments` and `get_frozen_workflow_graph` |
| Frozen workflow + live topology for one recommendation, refusing an incomplete topology | Component 3: `get_runtime_component_inputs` |
| Blast radius before and after | Component 3: `predict_failure_impact` (never recomputed here) |
| Candidates, feasibility, simulation, ranking, pair combinations, eligibility | Member 4: `live_pipeline`, `ranking`, `combinations`, `approval.eligibility` |

`LiveRecommendation.details` carries the display detail (root cause, current and simulated placement). It is never stored with an
approval and is not part of the proposal fingerprint; the `evidence` dict that approvals store is unchanged.

## Rules the page keeps

* Real evidence only. A missing frozen workflow, topology or failure-impact result is reported as missing; nothing is
  substituted, defaulted or estimated, and no workflow is built.
* **"No candidate" is not "healthy".** When the evidence is complete and the engine generates no candidate, the page says it is
  not a finding that the application is resilient: the engine only considers the change types it models.
* **No replica fix is invented.** Bookinfo's services run a single replica; the engine reports the spread change as infeasible
  (a spread cannot make one replica survive the loss of its node), predicts no improvement, and selects nothing.
* Nothing is applied automatically. A request for approval is a separate, explicit click, allowed only for a candidate the
  server says is ready, and is bound to the live evidence as before.
* Choices belong to one cluster. When the connected cluster changes (or the same connection reaches another cluster) the
  namespace, workflow and node choices are cleared, a late answer for an older request is dropped, and no recommendation is
  requested from a context that does not match its cluster.

## What is still blocked or not built

| Item | Why |
| --- | --- |
| A frozen workflow for the connected application | Component 1 must assess, approve and freeze one from real traces. Until then the page shows the missing evidence and says so; it does not build a workflow |
| A recommendation for single-replica services | A spread cannot help one replica, so it is reported infeasible. A **replica increase** (one more replica plus the spread that keeps it off the existing replica's node) is offered for such a service; it is `INSUFFICIENT_EVIDENCE` on the live page until the scheduling facts below are read, and several services failing together need the pair evaluated |
| Scheduling facts (node taints, readiness, declared intent) | Not carried by the topology contract, so a candidate that depends on them stays `INSUFFICIENT_EVIDENCE` and cannot be approved |
| Kubernetes YAML proposal | Generated by the engine (`yaml_proposal.py`) for an approvable candidate only, as a minimal Deployment patch. Nothing is generated for an infeasible, unverified or unranked candidate. The text is not applied |
| Validation (predicted versus measured) | The pipeline exists (`validation/`) and is exercised with a test double. **No real test-cluster executor exists**, so no validation against a real cluster has been run; nothing is applied automatically. See `docs/MEMBER4_OFFLINE_VALIDATION.md` |
| Survivability before/after as a separate figure | Reported blast radius is shown; survivability is not re-derived here |

## Additional candidates and every-failure panel (2026-10-23)

Completed: two further strategies reuse the existing feasibility search, simulation, ranking and YAML pipeline:

* `REPLICA_INCREASE_ANTI_AFFINITY`: add one replica with required hostname anti-affinity; needs a separate eligible node for each replica.
* `REPLICA_INCREASE_PLUS_2`: add two replicas with hostname topology spread; higher resource cost than adding one.

Both require a known concentrated service and settled replicas. Existing hostname spread or required anti-affinity prevents offering a variant that would overwrite that constraint. Missing scheduling evidence stays explicit; no shared schema changes are needed.

The **Assess every worker node** button evaluates one captured topology for the selected workflow, once per node. Each row shows baseline blast radius, whether that node is a single point of failure, candidate counts, its selected change and cost. **Show** opens that node's complete candidate ranking. Rankings from different failures are never combined. Switching workflows or clusters, or reloading evidence, clears the overview and discards old responses. The table scrolls in narrow Docker Desktop windows.

These are predictions. The overview does not request approval or apply changes. Apply and Reset remain separate explicit actions in the approval gate of the Docker Desktop extension. Controlled failure validation remains a separate experiment.

## Apply, rollout, analysis and Reset (2026-10-23)

The page is now one process: Review → Approved → Applying → Rollout → Analysis → Results. **Review** shows a *Proposal under review* summary (connected cluster, application,
workflow and whether it is frozen or declared, assessed failed node, strategy, target Deployments, proposed change, expected benefit, resource cost, feasibility), each fact
marked as a live observation, a user declaration, a model assumption, a prediction, or missing evidence. Approval stays separate from Apply. After an explicit Apply the
**Applied change** panel (outside the re-rendered region, keyed by the stored application) shows "Applying change…" at once, then the real rollout, the automatic
post-change analysis and a Before / Predicted / After comparison; Reset is explicit and has the same stages. The result is labelled *Post-change risk recalculated from live
topology*: observed placement plus Component 3's model, not an experiment. Full description: `docs/MEMBER4_APPLY_ROLLOUT_ANALYSIS.md`.

## The stepped page and its two views (2026-10-23)

The recommendations page is four steps in the order a person uses them: **1 Select application and workflow**, **2 Failure impact**, **3 Compare candidate
improvements**, **4 Review and approve proposal**, with a stepper that shows where the person is. The applied-change panel follows step 4. The stepper is drawn
from facts the page already holds (a choice was made, the status, the engine selected a candidate, an application is stored for it); a step is never marked done
without its own evidence, and a later step waits for an earlier one.

**Simple** (default) and **Research** are one page: the switch only sets `data-view` on `.rec-flow` and CSS hides the blocks marked `rec-research-only`
(contract chips, the metric cards, the per-candidate reasons, the ranking table, validation, the every-node overview, raw data, and each card's confidence and
score). Nothing computed or sent changes. The choice is remembered in the browser when storage allows. A test fails if a block of the page is neither a step nor
marked research-only. "Prediction, not observation" and the engine's validation note stay in both views.

Step 2 shows Component 3's own result for the assessed failure, carried unchanged as `baselineImpact` (blast radius, survivability, services and workflows
lost). **Nothing else is shown there**: the predictor measures no success rate, latency or error rate, and resource cost is a LOW / MEDIUM / HIGH label, not money.
Approve and Apply remain two separate actions by a named person; there is no single "Approve and Implement" button.
