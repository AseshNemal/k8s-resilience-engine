# Member 4: real-cluster validation protocol (design and readiness)

Owner: Member 4. This page is the plan for the first measured validation of Member 4's recommendations, and the gate that
stands in front of it. **Nothing described here as an experiment has been run.** No Kubernetes write and no node failure has
been executed, and no figure below is a result.

Research question: *what minimal feasible Kubernetes deployment change improves mandatory business-workflow survivability under
a single worker-node failure, and how accurately can we predict the improvement?*

## 1. What exists now (and what does not)

| Piece | State |
| --- | --- |
| Recommendation, feasibility, virtual what-if, ranking, YAML | Built, tested offline (17 controlled scenarios) |
| `ValidationRun`: approved plan, per-step authorization, fixed prediction, verdict | Built, tested on in-memory doubles |
| Revert of the applied change at run end; idempotent verdict | Built (audit fix) |
| Cluster designation pinned by `kube-system` UID; research cluster refused | Built, tested on doubles (`validation/cluster_identity.py`) |
| YAML-vs-live-Deployment check (whitelist, drift, no overwrite) | Built, tested (`validation/proposal_check.py`) |
| Node-fault kinds; cordon/drain refused as a "failure" | Built (`validation/node_fault.py`) |
| `KubernetesValidationExecutor` (all safety decisions) | Built, tested against an in-memory `ClusterGateway` |
| Experiment records (`validation_runs`), predicted and observed in separate columns | Built, tested on SQLite |
| `ClusterGateway` for ONE designated test cluster (`app/integrations/kubernetes/validation_gateway.py`): read-only by default, refuses the ambient credentials | Built; tested against a fake API server only. **Never run against a real cluster** |
| Kind worker `NodeFaultInjector` (`app/integrations/kind/`): `docker kill`, its own authorization per kill, durable recovery record, restore procedure | Built; tested with a scripted `docker` only. **Never stopped a real container** |
| End-to-end HTTP workflow probe (`validation/workflow_probe.py`, `app/integrations/probe/`), recorded beside the readiness-derived observation | Built; tested with fakes only. **Never sent a real request to a real application** |
| Measured-baseline support (`FAIL_BASELINE`) with a probe per failure episode | Built; tested on doubles |
| Composition, preflight, resource assessment, kind config (`experiments/member4_real_validation/`) | Built; the assessment and preflight were run read-only on the developer's machine. **The cluster has not been created** |
| A one-command experiment runner | Not built, deliberately: running is a person's step after the gates (see the runbook) |
| The fault-injection validation loop (`validation_loop/`, "Prove it" step): modes, one run approval, durable kill budget, background run, recovery, restricted Docker proxy, disruption guard, UI | Built; tested on in-memory doubles end to end (`tests/member4_validation_loop/`). **Never run against a real cluster; no real node stopped by it** |
| Comparison baselines (replica-count-only, deployment-only, dependency-only) | **Not implemented**; defined in section 4 |
| Any measured result | **None** |

## 2. What is measured, and what each side is derived from

| | Predicted | Observed |
| --- | --- | --- |
| Basis | Component 3 `predict_failure_impact` on the current topology and on the virtual topology of the selected candidate, fixed before anything is applied | The same Component 3 rules applied to pod readiness read from the cluster after the node was stopped |
| Service survival | per workflow service: survives / fails | per workflow service, from observed readiness |
| Workflow survival | mandatory workflow survives / fails | same |
| Blast radius, risk | percentage points, before and after; risk reduction = before − after | same, with the baseline's basis stated |
| Error | `prediction_error = abs(predicted blast radius after − observed blast radius after)`, percentage points | |

Stated limits, which any claim from these records must carry:

1. **The verdict's observed survival is readiness-derived, not request-derived.** A workflow "survives" when Component 3's rules say its
   mandatory services still have a ready replica. Whether a real request through the application succeeds is a different
   measurement, so an HTTP probe is recorded as a *separate, additional* observation (`observed.probes`, `comparison.probe`) and never
   replaces this one. It is built but has not been run. Whether a probe disagreement should block a verdict is undecided and must be
   decided before the first run (gate G10 in the runbook); until then the verdict is the readiness-derived comparison and the record says so.
   The probe's own limits: it reaches the workflow through one entry point, samples at the executor's poll interval, and a page that reports
   a dependency failure with HTTP 200 is only caught if the probe's `forbidden_text` matches the page.
2. **The baseline is measured only if a failure is injected before the change** (`FAIL_BASELINE`). Without it the observed risk
   reduction is partly a prediction (`DERIVED_FROM_PLACEMENT`) and the record says so. The real experiments should use a
   measured baseline.
3. **Single worker-node failure only.** No zone, network-partition, control-plane or multiple-node failure is covered.
4. **Timing is part of the measurement.** After a node stops, its pods stay `Ready` until the node controller marks the node
   NotReady (the node-monitor grace period, 40 s by default) and then marks its pods not ready; pod eviction and replacement
   follow only after the pod toleration period (300 s by default). The observation is the state after the node is NotReady and
   its pods are not ready, *before* replacements exist. The executor waits for exactly that and refuses to report a failure it did
   not observe. If a different observation instant is wanted (after replacement), it is a different, separately named experiment.
5. **A cordon/drain is not a node failure** and is refused as one. It is a graceful eviction (healthy kubelet, termination grace,
   PodDisruptionBudgets honoured). If it is ever run it is reported as "graceful eviction", never as a failure result.
6. **A stopped kind node is a crash-like failure on a shared host**, not a power loss on separate hardware: the node's container
   stops, its kubelet and network vanish, but all nodes share one kernel and one Docker VM.

## 3. Experiment matrix

One row per situation the research must cover. "Offline scenario" is the deterministic case in `tests/support/member4_scenarios.py`
that already encodes the situation and its *engine* expectation. That expectation is what the code is designed to do; it is **not**
evidence about a real cluster, and the real run's job is to check it.

| # | Situation | Offline scenario | Engine expectation (by design) | What the real run can show |
| --- | --- | --- | --- | --- |
| 1 | Replica concentration | `concentrated-replicas` | spread proposed, workflow predicted to survive | whether the scheduler actually spreads the pods, and the workflow survives |
| 2 | Correctly distributed replicas (negative control) | `correctly-distributed` | no candidate, nothing applied | that the baseline failure really leaves the workflow alive; a measured failure here would refute the model |
| 3 | Single-replica service | `single-replica`, `single-replica-increase` | spread infeasible; replica increase is the fix | that one replica fails and two (kept apart) survive |
| 4 | Topology spread | `concentrated-replicas`, `taints-tolerated` | feasible with the stated constraints | placement match (ready replicas per node) |
| 5 | Pod anti-affinity | `soft-anti-affinity-hardened`, `anti-affinity-too-strict` | offered only for soft→required; too-strict refused | for the strict case: replicas left Pending (an *inconclusive* run, not a validation) |
| 6 | Replica increase when necessary | `single-replica-increase`, `pair-of-replica-increases` | only for single-replica services | extra capacity actually available; the added replica placed apart |
| 7 | Failed scheduling / insufficient capacity | `insufficient-cpu`, `insufficient-memory`, `unschedulable-nodes`, `no-feasible-recommendation` | INFEASIBLE, no YAML | nothing is applied. To test the *executor's* handling of Pending pods, a deliberately infeasible patch needs a separate, explicitly named executor test, not a recommendation |
| 8 | Multiple mandatory dependencies | `multi-service-mandatory`, `spread-and-replica-increase` | one combined proposal | whether all mandatory services survive together |

Rows 1, 3, 4, 6 and 8 produce a measurable prediction-vs-observation pair. Row 2 is a negative control. Rows 5 (strict) and 7 test refusal
and Pending handling and yield **no validation verdict by design**: they must end `INCONCLUSIVE`/refused, and a "validated" there would be a bug.

Per experiment, in order:

1. Create the disposable cluster and the designation (section 6). Deploy the application with the declared layout for the row.
2. Compute the recommendation **against that cluster** (live path), request approval, and have a named person approve. The YAML is the
   server-generated text; nothing is edited.
3. Create the plan: the prediction is fixed here and written to the record (`PLANNED`) before anything changes.
4. `FAIL_BASELINE` (stop the node, observe, restore) so the baseline is measured.
5. `APPLY` → settle → observe the placement actually produced → `FAIL_NODE` → restore the node → revert the workloads. Each of
   `FAIL_BASELINE`, `APPLY` and `FAIL_NODE` is a separate authorization by a named person.
6. The record is finalized. A run that did not reach the end has no verdict and no error figure.

Repetition: the scheduler and timing are not fully deterministic, so each row should be repeated; the number of repetitions is a
choice that has to be justified before the first run (and recorded), and results reported as counts with their uncertainty (for
example agreement counts with a Wilson interval), not as a bare percentage. Order the repetitions so no row always runs first or last
on a freshly started cluster. Reset the namespace to the declared layout between repetitions.

## 4. Comparison methodology (not yet run, baselines not yet implemented)

Four methods are compared on the **same applications, the same injected failure and the same measured baseline**. Each method sees only
its own inputs and proposes a change; the proposal that is actually applied and measured goes through the same executor.

| Method | Uses | Question it can answer | Not implemented: what must be built |
| --- | --- | --- | --- |
| Replica-count-only | declared replica counts | "is every service replicated?" | a recommender that flags/raises services below a count |
| Deployment-only | pod placement and concentration (Member 2), no workflow | "which services are concentrated on one node?" | a recommender that spreads every concentrated service, ranked without workflow dependencies |
| Dependency-only | the workflow graph, no placement | "which services does the workflow need?" | a recommender that hardens mandatory services without knowing where pods run |
| **Proposed cross-layer** | workflow dependencies × placement (this system) | "which minimal change makes the mandatory workflow survive this node?" | **exists** |

The comparison must be pre-registered: how each baseline turns its inputs into a proposal is written down and reviewed **before** any
real run, so a baseline cannot be tuned after seeing results. The argument in the Development Lab that, for example, "dependency-only
cannot see which node matters" is a design argument, not a result; the claim is only made if the measured outcomes support it.

Metrics, identical for all four:

| Metric | Definition | Source in the record |
| --- | --- | --- |
| Workflow survival | observed: does the mandatory workflow survive the failure after the method's change | `observed.afterFailure.workflowSurvives` |
| Service survival agreement | per service, predicted vs observed survive/fail | `comparison.serviceSurvival` |
| Observed risk reduction | measured blast radius before − after (measured baseline) | `observed.riskReduction` |
| Prediction error | \|predicted − observed blast radius after\|, points | `prediction_error` |
| Minimality | replicas added, pods that change node, Deployments patched | `proposal`, `predicted.placement` |
| Cost | extra requested CPU/memory | recommendation snapshot |
| Refusals/inconclusive | runs that produced no verdict, with reasons | `status`, `failure` |

The baselines have no prediction of their own unless one is defined for them; for them the comparison is on **observed** risk reduction
and minimality only. Do not report a prediction error for a method that made no prediction.

## 5. Threats to validity (to be carried into the write-up)

* One small synthetic application (Bookinfo-shaped), one cluster type (kind), one failure type. Results do not generalize to production clusters.
* The prediction and the observation share Component 3's survival rules; agreement shows the *placement* model is right, and says nothing about
  whether those rules match real request behaviour (limit 1 in section 2).
* Rollout transients, HorizontalPodAutoscalers and PodDisruptionBudgets are not modelled.
* Scheduler non-determinism: the model predicts a verified placement; the scheduler may choose another that is equally valid. The record keeps
  the placement comparison separately from the outcome comparison for this reason.
* The thresholds for "agreement" (5 points) and the ranking weights are experimental defaults, not tuned on data. They must not be changed after
  seeing real results without recording that.

## 6. Gates before the first real validation test

Every item must be true and recorded. The executor refuses by itself where it can; the rest is procedure.

**Gate 1: code (built; review and dry run outstanding)**
1. The gateway, the kind injector and the probe now exist (see section 1) and are tested on doubles. What remains is a **review of the write
   calls** (strategic-merge patch with a `resourceVersion` precondition; the restore with `null` removals) and of `docker kill` before either is
   ever enabled, and a **read-only dry run** of the gateway against the disposable cluster once it exists: identity, baseline graph, Deployments, capacity.
2. The machine cannot hold the cluster as configured (resource assessment: INSUFFICIENT, see the runbook). That is gate G1 and it is open.

**Gate 2: the environment**
5. A **separate, disposable cluster** created for this work (for example a multi-node kind cluster), never the Docker Desktop cluster and
   never one that hosts anything else. At least one control-plane node and three worker nodes (so a 3-replica spread has somewhere to go
   and a failure leaves survivors).
6. Its own credentials file, outside the default kubeconfig path, with a context that is not `docker-desktop`.
7. A **designation** written by a person: cluster name, API server address, the UID of its `kube-system` namespace (read once from that
   cluster), the dedicated experiment namespace, and the node names. Not Kubernetes system namespaces.
8. The application deployed in that namespace in the declared layout for the experiment, healthy (every pod Ready, every node Ready).
9. Enough Docker VM memory for the second cluster beside the research cluster (a multi-node kind cluster is heavy). Check before creating
   it; a starved VM produces NotReady nodes that look like failures.
10. A **separate backend instance and database** for this work (its own `DATABASE_URL`, credentials pointing at the disposable cluster) so
    recommendations and approvals are computed from *that* cluster's live evidence. Approvals made against any other cluster cannot be used:
    the executor checks the proposal against the Deployments actually present.
11. A frozen workflow (Member 1) for the application. Member 4 does not create one.

**Gate 3: explicit approvals by named people**
12. The recommendation is **approved** through the existing gate by a named person (no automatic apply).
13. Separate authorizations for `FAIL_BASELINE`, `APPLY` and `FAIL_NODE`, each bound to the plan digest and the target.
14. `writes_enabled` is set deliberately by the person starting the run.
15. The repetition count, thresholds and the baseline definitions are written down before the first run.
16. A stop rule: any `needsManualRestore` ends the session until a person has restored the cluster by hand.

The full gate list, the creation commands, the run order and the abort/recovery procedure are in `docs/MEMBER4_KIND_VALIDATION_RUNBOOK.md`.

If the cluster must be the Docker Desktop one, that is a *separate* decision: a `ResearchClusterAuthorization` naming its UID, with a reason,
and an explicit acceptance that a node will be stopped in the environment the rest of the project uses. This protocol does not recommend it.

## 7. Phase 5: two experiences, and how real reports will be shown

| | Development Lab | Production Recommendations |
| --- | --- | --- |
| Data | hand-written scenarios, scripted scheduler | the connected cluster and approved frozen workflows only |
| Evidence source | `offline-test-fixture` (never approvable) | `live-discovery` |
| Validation | scripted test double, labelled as such | none unless a real record exists |
| Where | `experiments/member4_dev_lab/`, local only | `app/` routes and the extension page |
| Shared | nothing imports the lab from `app/` or `extension/` (tested) | |

Future display of real validation reports (not built):

* Source: `ExperimentRepository.list_measurements()` only, which returns completed runs whose `evidence_class` is `REAL_CLUSTER`. A test-double
  run is stored but can never come back from that call, so a simulated run cannot be shown as an experimental result by accident.
* Shape: a read-only route and a section on the Recommendations page, listing for each experiment the cluster, the approval, the **predicted**
  and the **observed** values in two labelled columns, the error, the basis line for each side (section 2), and the limits.
* Runs that did not complete (failed, inconclusive, refused, aborted) are shown as such, with the reason and with **no** verdict or error
  figure; a run needing a manual restore is shown prominently.
* The shared `ValidationReport` contract (five fields) is unchanged; `to_validation_report` reduces a completed result to it.
* Dev Lab validation sections keep their "scripted double" label and are never mixed into that list.
