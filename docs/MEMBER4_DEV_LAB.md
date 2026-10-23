# Member 4 Development Lab (offline, development only)

An interactive, local-only walkthrough of the Member 4 — Recommendation Evaluation & Validation
pipeline. It serves the 17 controlled scenarios of `tests/support/member4_scenarios.py` through the
**real** engine (`live_pipeline.evaluate_recommendation`, Component 3's `predict_failure_impact`,
`yaml_proposal.build_yaml_proposal`, `validation.ValidationRun`) so every stage can be inspected and
demonstrated.

## Start

```bash
./scripts/dev-lab.sh          # or: scripts\dev-lab.cmd on Windows
# equivalent: python -m experiments.member4_dev_lab   (DEV_LAB_PORT to change the port, default 8741)
```

Open http://127.0.0.1:8741.

## Isolation guarantees

- The lab is a **standalone app** (`experiments/member4_dev_lab/`). Nothing in `app/` or `extension/`
  imports it, so the production server and the installed Docker Desktop extension cannot serve it.
- It binds to `127.0.0.1` only and serves **GET routes only** — there is nothing to approve, apply or
  write through it.
- It never reads a cluster or a kubeconfig. All inputs are the hand-written offline scenarios; the
  "observation" in the validation section comes from the scripted in-memory test double
  (`tests/support/member4_test_cluster.py`).
- Every evaluation is stamped `offline-test-fixture`. The approval gate and the YAML generator trust
  only `live-discovery`, so lab YAML is always `applicable: false` and lab recommendations can never be
  approved. The scripted validation walks the real approval gate via the test-only relabelling helper
  `tests/support/member4_approvals.as_live`, which the lab UI exposes only as a labelled explanation.
- The production Recommendations page and its live endpoints are untouched.

## What each section shows

| Section | Content | Source |
| --- | --- | --- |
| A | Scenario objective, workflow criticality, mandatory/optional services, nodes, replicas, placement, failure domain, supplied scheduling evidence | scenario fixture |
| B | Current risk: failed node, blast radius, survivability, failed services/workflows, root causes, node risk ranking, placement diagram | Component 3 `predict_failure_impact` |
| C | Every candidate: strategy, target, proposed change, feasibility status/findings/missing evidence, cost, confidence, approval eligibility | `generate_candidates`, feasibility checks, `describe_live_recommendation` |
| D | Before/after placement diagrams, predicted blast radius, risk reduction, assumptions and not-modelled limits | `evaluate_candidate` simulation + `FeasibilityResult` |
| E | Ranking table with score components, exclusion reasons, selection justification; configurable weights re-evaluate the scenario | `ranking.rank_candidates` |
| F | The generated YAML with copy button, sha256, disclosures, `applicable: false` eligibility status | `build_yaml_proposal` |
| G | Predicted vs scripted-observed: service/workflow survival, blast-radius and risk-reduction comparison, prediction error, verdict — under both scripted schedulers | `ValidationRun` on the in-memory double |

The **Experiment overview** tab compares all 17 scenarios
(Scenario · Relevant inputs · Detected problem · Candidates · Selected · Action/validation · Expected result)
and marks the replica-count-only, deployment-only and dependency-only baselines as *not evaluated*
(they are not implemented; only the proposed cross-layer method is).

## Reading the validation numbers honestly

Under the scripted `spread` scheduler the prediction error is `0.0` **by construction**: the double places
pods exactly the way a working constraint tends to. That proves the comparison arithmetic and the gate
wiring — not model accuracy. The `stay` scheduler shows the opposite extreme. Real accuracy numbers need a
real test cluster and a controlled node failure.

## Troubleshooting

- *Port in use*: `DEV_LAB_PORT=9000 ./scripts/dev-lab.sh` (or `./scripts/dev-lab.sh 9000`).
- *ModuleNotFoundError*: run from the repository root with the project virtualenv active.
