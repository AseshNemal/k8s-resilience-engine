# Member 4: offline harness, YAML proposals and the validation pipeline

Owner: Member 4. This page says what was built so that Component 4 can be developed and tested **without a frozen workflow
from Component 1 and without touching a cluster**, how that is kept out of the product, and, just as important, what has
**not** been shown. No shared contract, no other member's algorithm and nothing in the live cluster was changed.

## Keeping it out of the product

| Guarantee | How it is enforced |
| --- | --- |
| The harness is not a fake live-data provider | It lives in `tests/support/`. `app` never imports it (`test_the_product_never_imports_the_harness`). The page and the live routes still read only the connected cluster and approved frozen workflows |
| Harness output can never be approved | Every offline evaluation carries `evidence.source = "offline-test-fixture"`. The approval gate trusts only `"live-discovery"` (`has_verified_provenance`); with the label forged, three isolation tests fail |
| Offline YAML is never "applicable" | `YamlProposal.applicable` is true only for live evidence. Offline text is generated for research and says so |
| The one place that fabricates verified provenance is test-only and named | `tests/support/member4_approvals.py`, used to start validation tests from an approval the real gate produced |
| The extension is unchanged | The Recommendations page still shows the evidence blocker when a workflow or the topology is missing |

## What exists (and where)

| Step | Module | Notes |
| --- | --- | --- |
| Evaluate artifacts without fetching | `live_pipeline.evaluate_recommendation` | The live route calls it with live inputs; the harness calls it with controlled ones. Same code |
| Strategies | `strategy_feasibility.py`, `candidate_generation/` | See "Strategies" |
| Feasibility | `feasibility_analyzer.py` | One constraint-aware search: taints, tolerations, capacity, selectors, node and pod affinity, existing spread, readiness |
| Virtual model | `mutations.py`, `capacity_ledger.py` | Always a deep copy; the original `DeploymentGraph` is never changed |
| Re-evaluation | Component 3's `predict_failure_impact` | Called, never reimplemented |
| Ranking and selection | `ranking.py`, `combinations.py` | Single candidates and bounded pairs; the less restrictive strategy wins a tie |
| YAML proposal | `yaml_proposal.py` | Minimal Deployment patch; see below |
| Approval bound to the evidence | `approval/` | Proposal fingerprint plus the server-generated YAML, both re-checked when a request is decided |
| Validation | `validation/` | Fixed prediction, authorizations, injected executor |
| Harness and benchmark | `tests/support/member4_*.py`, `experiments/member4_offline_benchmark/` | Test and research only |

### Strategies (an escalation, not a menu)

* **`TOPOLOGY_SPREAD`** for a concentrated service: `topologySpreadConstraints` on `kubernetes.io/hostname`, `maxSkew 1`, `DoNotSchedule`.
* **`REPLICA_INCREASE`** only for a service that declares and runs exactly one replica ("only when necessary"): no change of placement
  can help one replica. The patch raises `replicas` **and** adds the spread constraint that keeps the added replica off the existing
  replica's node, because replicas alone may be co-located. The added replica is not counted as placement churn; its cost is `MEDIUM`.
* **`POD_ANTI_AFFINITY`** only for a Deployment that already declares *soft* anti-affinity: required hostname anti-affinity admits a
  **strict subset** of the placements a hostname spread admits (one per node implies a skew of at most one), so it never succeeds
  where a spread fails and gives the same placement where both do. A sweep test enforces that. It is therefore not offered for every
  concentrated service; it is offered to harden a rule the owner already asked for softly, and the spread wins a tie.
* **Redistribution** is not a separate candidate type. A spread (or anti-affinity) constraint is the scheduling policy; the pods
  predicted to change node are disclosed as a **separate step that is not part of the patch** (`podReplacement.included = false`).
* **Combinations**: at most two members, spread and replica increase can be mixed, and the second member is checked against the
  capacity the first consumed (the ledger debits an added replica's request). Anti-affinity is not pairable.

### YAML proposal: when there is none

A proposal exists only for a candidate the approval gate would accept (verified `FEASIBLE`, ranked, positive score, fingerprint
recorded). Otherwise there is **no text at all**, with a code: `INFEASIBLE`, `INSUFFICIENT_EVIDENCE`, `NOT_ELIGIBLE`,
`CANDIDATE_NOT_FOUND`, `DEPLOYMENT_UNRESOLVED` (the Deployment or its labels are not in the evidence), `ALREADY_CONSTRAINED` (a strategic
merge would rewrite an existing constraint; Kubernetes replaces required anti-affinity lists as a whole, so the existing terms would be
lost).

The text never claims a pod was moved. Its header and `disclosures` say the patch edits the pod template (the Deployment rolls out), does
not move running pods, and that the scheduler decides where replacements land. A replica increase adds that an autoscaler owning the
replica count could revert it.

At request time the **server** generates the YAML; a different client-supplied text is refused. When the request is decided the server
generates it again from a fresh evaluation; a Deployment that changed in between makes the approval stale even if the placement plan did not.

### Validation pipeline

`ValidationPlan.from_approval` accepts only an `APPROVED` request with verified provenance whose fingerprint and YAML still match a fresh
evaluation, aimed at a target whose environment is `test`. `ValidationRun` then needs a separate, named `Authorization` for each step
that writes or breaks something (`APPLY_TO_TEST_CLUSTER`, `FAIL_NODE_IN_TEST_CLUSTER`, optionally `FAIL_BASELINE_IN_TEST_CLUSTER`), each
bound to the plan digest, the action and the target. It re-checks the approval immediately before the write. A node taken down is always
restored in a `finally`; a failed restore is reported as `needsManualRestore`.

What is compared (all against a prediction fixed before the run): service survival, mandatory-workflow survival, blast radius, risk
reduction and `predictionError` (absolute blast-radius difference after the change, in points), under configurable thresholds. The
observed outcome is derived from observed pod readiness by Component 3's own predictor. If no failure was injected before the change, the
baseline is labelled `DERIVED_FROM_PLACEMENT` and the risk reduction carries a caveat; a measured baseline needs its own authorization.

Runs that do not finish are `INTERRUPTED`, `FAILED` or `INCONCLUSIVE` and are never `validated`. The shared `ValidationReport` contract is
unchanged; `to_validation_report` reduces a completed run to it.

## Running it

```bash
python -m pytest tests/recommendation_evaluation_validation              # unit, scenario matrix, safety
python -m experiments.member4_offline_benchmark.run_benchmark            # the benchmark as a table
python -m experiments.member4_offline_benchmark.run_benchmark --json     # the same, machine-readable
```

The catalogue (`tests/support/member4_scenarios.py`) has one deterministic case per situation: concentrated, distributed, single replica
(spread, and replica increase), insufficient CPU and memory, cordoned and NotReady nodes, taints blocking, tolerated and unread, a node selector
that leaves one node, soft anti-affinity hardened, anti-affinity with more replicas than nodes, a pair of mandatory services, a mixed pair, and no
feasible recommendation. Stale approvals and failed, interrupted, unsettled and unreadable validation runs are covered in
`test_validation_safety.py`.

## What is verified, and how far

**Implemented and tested** (unit and integration tests, plus mutation checks that the guards fail when removed): the offline evaluation core,
all three strategies and pairs, ranking and its tie-break, the YAML generator and its refusals, server-generated YAML bound to the approval,
the authorization and lifecycle rules of a validation run, and the predicted-vs-observed arithmetic.

**Verified only with controlled test data**: every number the benchmark prints. The scenarios are hand-written and the "test cluster" is an
in-memory double whose scheduler is scripted (`spread` agrees with the prediction, `stay` and `pack` do not). A prediction error of `0.0` there
shows the comparison is computed correctly; it is **not** evidence that the engine predicts a real scheduler. The Bookinfo-shaped benchmark
models that application's shape with several replicas; it is not a measurement of Bookinfo.

**Not built, and requires real-cluster experimental validation**:

* a real `ValidationExecutor` (applying a patch to a test cluster, failing a node, reading readiness) and a way to identify a cluster as a test
  environment. Until it exists no change has been applied and no observation made on a real cluster;
* any claim that predicted and observed outcomes agree on a real cluster, including prediction-error figures, the thresholds' defaults (they are
  experimental, not tuned) and the ranking weights;
* persistence of validation runs, and any API route or page for them (the page still says nothing has been applied);
* scheduling facts the live topology does not carry (node taints, readiness on the discovery path, declared intent), without which live
  candidates remain `INSUFFICIENT_EVIDENCE` and cannot be approved, as before;
* more than two members in a combination, rollout-time feasibility, autoscalers and PodDisruptionBudgets (stated as not modelled on each result).
