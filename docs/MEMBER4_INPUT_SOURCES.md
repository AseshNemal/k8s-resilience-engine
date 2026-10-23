# Member 4: where each input comes from, and what is independent

Owner: Member 4. The rule this page follows: **use another member's data when their component can give it; build an independent
path only when it cannot; change no one else's code.** Members 1 to 3 are called through their public interfaces and are not modified.

## 1. What Member 4 consumes

| Input | Owner | Public call used | Available? | Independent path |
| --- | --- | --- | --- | --- |
| Where pods run, and how complete that evidence is | Member 2 | `discover_live` (the call Member 3's provider makes) | Yes | none needed |
| Node labels, Ready, schedulable, free CPU and memory; each service's declared intent | Member 2 | `analyze_live_topology` (their fuller analysis) | Yes | none needed |
| Failure impact (blast radius, survival) | Member 3 | `predict_failure_impact` | Yes | none needed |
| **Frozen workflow** | Member 1 | `get_frozen_workflow_graph` | **No: no traces, so nothing was assessed or frozen** | **A declared workflow** (section 3) |
| **Node taints** | nobody | none exists | No | **A read-only taint reader** (section 2) |

Measured on the developer's machine on 2026-10-23 (read-only): Jaeger holds only its own service (`jaeger-all-in-one`), no Bookinfo
spans; `istio-system` is empty; no Bookinfo Deployment declares resource requests; only the control-plane node carries a taint.

## 2. Scheduling facts for feasibility (live path)

`live_pipeline.build_live_recommendation` still takes the workflow, the topology and the completeness check from Member 3's provider.
Before evaluating, Member 4 now enriches a **copy** of that graph (`live_scheduling_evidence.py`):

* node labels, Ready state, schedulability, free capacity and declared intent are taken from Member 2's `analyze_live_topology`;
* node taints are read by Member 4 (`app/integrations/kubernetes/node_taints.py`), read-only, through the existing client.

Rules, each tested: nothing observed is overwritten; a fact Member 2 could not give stays unknown and the feasibility check says
`INSUFFICIENT_EVIDENCE` for it; capacity is only ever reduced, never loosened; an unschedulable node stays unschedulable; pod placement is
never touched; a node with no `spec` or an unreadable taint is **unread**, never "no taints"; if the second read fails nothing is
enriched and `evidence.schedulingFacts.unavailable` says why.

Containers that were read and declare no requests (stock Bookinfo) are a fact: a pod that requests nothing needs no capacity, and the
verdict is reached. Containers that were not captured at all are missing evidence and are named.

**Observation for Member 2's owner (not changed by Member 4):** `analyze_live_topology` reports a node whose CPU or memory could not be read
as `0.0`, while `discover_live` keeps it unknown. Taken at face value that turns "unknown capacity" into "no capacity". Member 4 does not
take capacity from the fuller analysis where the evaluated graph does not know it. The docstring of `add_scheduling_evidence` in
`tests/recommendation_evaluation_validation/test_live_recommendation.py` described discovery as lacking intent and labels; that was true of
`discover_live` and is why the second read exists.

## 3. A declared workflow when Member 1 has none

`declared_workflow.py` reads a JSON file that the **backend's environment** names (`RESILIENCE_DECLARED_WORKFLOW_PATH`, never a request) into
the unchanged `WorkflowGraph` contract. It requires who declared it and a criticality stated by them (there is no default), rejects
inconsistent files, and records a digest of the file. `experiments/member4_real_validation/declared-workflow.bookinfo.example.json` is an
example that matches the project's own Bookinfo workflow; it is not loaded unless a copy is named.

What a declaration is: the developer's statement. Nothing checked that the dependencies are real, that "mandatory" is right, or that the edges
exist; an error in the file is an error in every number derived from it.

How it is used:

* `build_declared_recommendation`: Member 2's live topology + the declaration + Member 3's `predict_failure_impact`, labelled
  `declared-workflow`;
* `GET /api/recommendations/declared` and `/declared/detail`; the context lists `declaredWorkflows` apart from the frozen ones, never selects
  one for you, and the page labels it and shows a standing note.

What it can never do at the production gate (the research tier of section 4 is a separate, narrower door). That gate trusts only `live-discovery`. A declared evaluation is refused at its `approve`; the request route only
ever evaluates frozen workflows, so a declared id sent to it is refused with `WORKFLOW_EVIDENCE_UNAVAILABLE` and no record is made; the
declared detail forces `approval.ready = false` on every candidate with the reason; its YAML is generated for reading and marked not applicable.
The engine's own eligibility verdict does not look at the evidence source (the gate does, later), which is why that override is made at the route.

## 4. The research approval tier (approved by the developer, 2026-10-23)

Everything up to the approval step works without Member 1. To let Member 4 approve, and so validate, until Member 1 can freeze a workflow,
there is a second, separately named gate: `approval/research.py`, `ResearchApprovalService`. It does **not** widen the production gate.

What stays exactly as it was: `ApprovalService`, its routes, `has_verified_provenance` (live evidence only) and their tests. The production
`approve` still refuses a research record (`APPROVAL_PROVENANCE_UNVERIFIED`); the research gate refuses a production record. The only change to
production code is two hooks in `ApprovalService` whose defaults are today's behaviour (`_require_provenance`, `require_applicable_yaml`).

Everything the production gate checks is checked here too: verified feasible, ranked, bound to the exact plan and predicted effect, server-generated
YAML regenerated at decision time. Four things differ, on purpose:

1. the evidence must be a **declared-workflow** evaluation, never a live one;
2. the request carries a **named person's written acknowledgement** (at least 20 characters) that the workflow is declared, not observed; the anonymous
   `developer` is refused, and so is an anonymous decision;
3. the approval is bound to the **declaration's digest**: it is evaluated again when decided, and a declaration that changed since makes it stale;
4. the YAML may be marked "not applicable" (it always is for declared evidence), which only keeps it from being applied to the connected cluster.

Routes (Member 4's own request bodies; no shared schema changed): `POST /api/recommendations/declared/{id}/request-approval`, `/approve`, `/reject`,
`GET /declared/{id}/approval`. The declared detail offers the door per candidate (`researchApproval`) while the production controls stay off.

Scope. A research approval is honoured only by `ValidationPlan.from_approval(..., allow_declared_workflow=True)`, which still requires a designated
test target. The plan is refused if the declaration changed, or if the workflow handed in is not the declared one the evaluation used. Every experiment
record made from it says `workflowBasis: DECLARED`, `conditionalOnDeclaration: true`, and keeps the acknowledgement and the digest;
`ExperimentRepository.list_measurements(workflow_basis=...)` keeps DECLARED and FROZEN results apart. **Whatever such an experiment shows is conditional on
the declaration being correct.** For Bookinfo the architecture is documented, which makes that defensible for a placement-prediction study; it is still a claim about
the declaration, not about discovery. When Member 1 can freeze a workflow, frozen results are the stronger evidence and should be reported separately.

## 5. Status

Tested with fakes and, read-only, against the real docker-desktop cluster: the taint reader, Member 2's analysis, a declared evaluation, the HTTP routes, and
the research request, approval and validation-plan build on real Bookinfo data (approvals went to a throwaway database). **Never exercised against a real cluster:**
applying the YAML and stopping a node (the executor), which waits on the readiness gates in `docs/MEMBER4_KIND_VALIDATION_RUNBOOK.md`. The installed extension is
unchanged: nothing on the live page changes until it is updated and the declaration file is given to the backend.
