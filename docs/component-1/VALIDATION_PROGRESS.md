# Component 1 validation progress

Date: 2026-10-08. Branch: `Workflow-dependency-modelling/Validation`.

This branch verifies and corrects the Phase 5 implementation before the
manifest-driven experiments planned for Phase 6. It does not replace Phases
6–8 or claim final empirical validation.

## Validation checkpoints

### Frozen evidence provenance

- Frozen `traceIds` now contain only correlated executions whose workflow
  outcome was eligible for dependency assessment: `SUCCEEDED` or `FAILED`.
- Correlated but `INDETERMINATE` executions remain auditable through the
  excluded count and are not described as valid assessment evidence.
- Duplicate correlated trace identities remain a freeze-time error, including
  duplicates attached to excluded outcomes.

### Structural evaluation metrics

- Graph evaluation now reports complete root-to-leaf path precision, recall,
  and F1 in addition to service and edge metrics.
- Per-workflow reconstruction accuracy is `1.0` only when both the discovered
  service set and directed edge set exactly match the independent reference.
- The existing `accuracy` field remains compatible, while the same set-based
  value is now named explicitly as `jaccard_similarity` for research reporting.

### Coverage-aware classification reporting

- Multiclass `accuracy` remains available for compatibility and is now also
  exposed explicitly as `selectiveAccuracy`.
- `classifiedCount` and `expectedCount` accompany coverage and abstention so a
  high conditional score cannot conceal unevaluated reference services.

### Mandatory-edge validity

- Mandatory-edge metrics are no longer inferred from the labels of their
  endpoint services.
- Each directed edge requires its own confirmed intervention cohort before it
  can be classified as mandatory, optional, or supporting.
- Missing edge-level intervention evidence remains `insufficient_evidence`;
  service classifications are never copied onto adjacent edges.
- Explicit edge-validation runs cannot be approved while candidate edges are
  unresolved, reuse an intervention batch, target an unobserved edge, or
  contradict the validated service labels.
- Approved mandatory and optional edge labels are persisted independently and
  mandatory edges must exist in the exported graph.
- Approved graphs can now be evaluated against the independent reference graph
  through the dependency-assessment graph-evaluation endpoint, producing real
  mandatory-edge precision, recall, F1, and accuracy.
- Legacy frequency-only evaluations continue to report
  `not_evaluated_no_predicted_edge_labels` rather than fabricating labels.

### Validated artifact version

Frozen artifacts produced after the edge-validation corrections use algorithm
version `workflow-dependency-modeling/phase-5-validation-2` so their provenance
is distinguishable from the original Phase 5 and earlier validation behavior.

### Real Jaeger evidence correlation

- A real Online Boutique checkout completed successfully and its injected trace
  ID resolved directly in Jaeger with 56 spans.
- The original campaign fetcher discarded that trace ID and searched a bounded
  service/operation result set, producing a false `missing` correlation while
  the exact evidence existed.
- Campaign trials now retrieve `/api/traces/{trace_id}` evidence directly and
  poll for a bounded ingestion window before declaring it unavailable.
- Outcome timestamps cannot precede the completed workflow invocation.

### Real trace-quality eligibility

- One external W3C parent is treated as a valid distributed-trace boundary;
  additional local roots remain partial evidence.
- Client targets without corresponding server-side process identities are now
  reported explicitly instead of disappearing from the candidate graph.
- Live evidence exposes its per-trace quality summary before any dependency
  necessity claim is allowed.
- Analysis sessions persist that summary and route incomplete evidence to
  `repair_trace_instrumentation` instead of success-condition confirmation.
- Legacy sessions without a quality assessment remain readable but are marked
  `NOT_ASSESSED` and ineligible for dependency validation.

## Remaining roadmap boundary

The validation branch now contains the corrected service, graph, path, and
explicit mandatory-edge evaluation semantics. Phase 6 remains responsible for
manifest-driven reproducibility, deterministic partial-trace degradation,
evidence-count experiments, immutable raw inputs, and machine-readable result
packages. Phases 7 and 8 remain the operational handoff and final empirical
study described in the development plan.

---

# Master audit update: 2026-10-12

This section records the repository audit, minimal correction, and tests run in
response to the Component 1 audit brief. It supersedes neither the earlier
historical records nor a future controlled Online Boutique campaign. Results
below distinguish current execution, repository inspection, synthetic-fixture
tests, and unperformed empirical work.

The approved topic-assessment PDF was also inspected. Pages 3 and 6 confirm the
scope used here: workflow definitions plus OpenTelemetry/Jaeger traces,
mandatory versus optional/supporting classification, graph validation, and
precision/recall/F1/edge-level accuracy. The PDF describes planned experimental
evaluation; it does not supply completed Component 1 results.

## Baseline before this update

- `git status --short` was clean at `5f1fb9d`.
- The documented root command `python -m pytest -v` did not establish a passing
  baseline in the active environment. Pytest recursively collected third-party
  tests under `tmp/`, and application imports failed because the active Python
  environment did not contain `kubernetes`. The interrupted collection reported
  202 project/third-party items, 97 collection errors, and one skip. This is an
  environment/test-discovery result, not an algorithm failure.
- The active versions also differ from `requirements.txt` (for example FastAPI
  0.125.0 instead of 0.139.2 and Pydantic 2.12.5 instead of 2.13.4). The
  requirements file does not declare the Kubernetes client even though Member
  2 imports it at module import time.
- API tests that use the active FastAPI `TestClient` did not complete within 30
  seconds and were interrupted. Direct route/service calls complete. No API
  test is reported as passed from those interrupted runs.

## Architecture and file-level audit

| Area and location | Verified behavior | Generality | Finding / severity | Disposition |
| --- | --- | --- | --- | --- |
| Public entry point: `app/engine/workflow_dependency_modeling/service.py` | `build_workflow_graph(scenario, traces)` validates evidence, parses spans, reconstructs interactions, creates reference-free candidates, and returns the shared `WorkflowGraph`. | Generic scenario and service identifiers. | The legacy shared graph fields still name frequency candidates `mandatoryServices`; downstream users must consume only approved/frozen graphs as validated truth. High research-validity risk if bypassed. | Existing approval/freeze lifecycle retained; sample artifact is explicitly labelled candidate-only. |
| Jaeger parsing: `trace_reader.py` | Reads wrapped or bare Jaeger JSON, trace/span/parent IDs, process service identity, operation, duration, tags, and error tag. | Jaeger-specific adapter. | Start timestamp, end timestamp, non-`CHILD_OF` links, and a normalized span status are not retained in `Span`. Asynchronous link causality is therefore not supported. Medium. | Documented limitation; no unsupported causal inference added. |
| Quality gate: `trace_validator.py` | Rejects missing trace/span IDs and duplicates; excludes partial traces with broken parents, ambiguous roots, unresolved identities, or unmatched client targets. | Structural and name-independent. | Strict exclusion protects the primary denominator but may reduce coverage; this is not evidence that missing dependencies do not exist. | Retained and covered by tests. |
| Correlation/discovery: `auto_discovery.py`, execution/campaign services | Groups by trace ID, selects explicit operations, records exact correlation and business outcomes. | Workflow registration is data-driven. | Business semantics still require a reviewed success contract; operation discovery alone cannot infer a business workflow. Medium if discovery is presented as validation. | UI/service terminology uses candidate/unverified states. |
| Interaction extraction: `interaction_extractor.py` | Resolves direct parent-child cross-service calls, deduplicates logical edges per trace, records broken parents, and avoids all-to-all trace edges. | Name-independent. | `FOLLOWS_FROM`/span links are not modeled; async relationships remain unknown. Medium. | Documented, not guessed. |
| Candidate classification: `dependency_classifier.py` | Counts per-trace service presence and labels frequency-based mandatory/optional *candidates* without accepting reference labels. | Generic threshold algorithm. | Frequency cannot prove necessity. The compatibility `WorkflowGraph` can be misread if provenance is omitted. High. | Validated classification remains a separate intervention-based lifecycle. |
| Validated classification: `dependency_assessor.py` and assessment models | Uses eligible baseline/intervention outcomes, confirmation gates, sample gates, explicit effects, reasons, exclusions, and separate edge interventions. Supports mandatory, optional, supporting, and insufficient evidence. | Generic cohort interface. | No first-class conditional label or branch-condition model. Conditional dependencies cannot yet be exported responsibly. High for the brief's conditional requirement. | Unresolved; requires a reviewed contract extension rather than mapping partial effects to conditional. |
| References: `sample_data/workflow_references/*.json` | Stored separately from scenarios/evidence and never supplied to production reconstruction. | Per-application reference adapter. | All current references are `synthetic: true` and lack an independently reviewed empirical evidence package. Their scores cannot establish Online Boutique accuracy. Critical empirical limitation. | Kept as deterministic unit-test ground truth only. |
| Evaluation: `evaluator.py` | Separates discovery, mandatory-service, graph, path, and validated mandatory-edge metrics; reports FP/FN details. | Generic directed set evaluation. | Before this update `accuracy` was Jaccard and had no TN universe. High metric-validity defect. | Corrected with explicit evaluated non-edges, TN, evaluated count, and an accuracy-definition field. |
| Shared export: `graph_builder.py`, `app/shared/schemas/workflow.py` | Produces deterministic, duplicate-free directed edges and validates the agreed camelCase schema. | Shared contract preserved. | The shared contract has no evidence/provenance/unknown/conditional fields. Changing it requires team approval. Medium. | No breaking schema change; evidence stays in Component 1 assessment artifacts. |
| Benchmark adapter: `online_boutique_workload.py`, campaign service | Contains reviewed Online Boutique checkout products, HTTP sequence, allowed hosts, and a named benchmark profile. | Intentionally benchmark-specific adapter around generic engine ports. | Online Boutique names/paths exist in the adapter and demo defaults, not the core classifier/evaluator. Low when correctly scoped. | Retained and explicitly documented as adapter configuration. |
| Demo trace seeding: `app/integrations/jaeger/client.py` | Can seed named synthetic Boutique-like traces for UI/demo use. | Benchmark-specific demo. | A Jaeger round trip does not make seeded traces real application evidence. High if mislabeled. | Existing source labels retained; excluded from empirical claims. |

## Metric correction implemented

`ReferenceWorkflow` now accepts `evaluatedNonEdges`, kept separate from positive
`expectedEdges`. The four synthetic references explicitly enumerate every
directed non-self edge among their labelled services that is absent from the
designed graph. The evaluator now reports:

- `trueNegatives`;
- `evaluatedCount`;
- `accuracyDefinition`;
- confusion-matrix accuracy over an explicit independently labelled universe.

Predictions outside that universe count as false positives. Unknown reference
edges are not silently converted to negatives. Set comparisons without a
negative universe remain backward compatible but explicitly report
`jaccard_no_negative_universe`.

## Reproducible test plan and results

| ID | Objective / input | Current result | Status and evidence |
| --- | --- | --- | --- |
| A | Normal successful workflow; actual Online Boutique trace | A prior repository note records one 56-span checkout correlation, but no immutable raw trace package was available or rerun in this audit. Synthetic checkout parsing/export passed. | PARTIAL. Historical note above; current synthetic artifact under `experiments/component-1/audit-2026-10-12/`. |
| B | Repeated observations and duplicate-safe aggregation | Per-trace set aggregation and repeatability logic passed in deterministic tests. No repeated live run was performed. | PARTIAL. Core test run and selected repeatability test. |
| C | Optional/non-blocking dependency | Synthetic frequency candidates and controlled outcome assessment distinguish optional from mandatory. | PASS for unit logic; NOT TESTED empirically. Selected Phase 4 test. |
| D | Required dependency failure | Controlled outcome fixtures classify repeatable failure effects as mandatory and exclude unmatched evidence. | PASS for unit logic; NOT TESTED with live fault injection. Selected Phase 4 tests. |
| E | Conditional dependency | Supporting/partial effect is represented, but there is no branch-condition/conditional classification. | FAIL for the requested first-class conditional capability. |
| F | Incomplete trace | Partial/invalid traces are diagnosed and excluded instead of treated as negative evidence. | PASS. `test_trace_validator.py`. |
| G | Noise, retries, repeated calls | Logical edges are deduplicated per trace and service presence is counted once per trace. | PASS for deterministic unit behavior. `test_interaction_extractor.py`. |
| H | Unseen workflow | Scenario/operation interfaces accept arbitrary workflow IDs without core changes; business meaning must be registered. | PASS for interface behavior; NOT TESTED on a new real workflow. |
| I | Unseen service identity | Tests use non-Boutique identities (`gateway`, `orders`, etc.) and edge artifacts round-trip without benchmark-specific names. | PASS for naming independence; does not prove second-application compatibility. |
| J | Malformed input | Invalid thresholds, missing/duplicate IDs, missing evidence, broken parents, and invalid manifest controls fail explicitly. | PASS for covered cases. Core and Phase 6 tests. |

Commands actually executed after the correction:

```text
python -m pytest -q --basetemp tmp\pytest-audit-20261012-core <seven core test modules>
78 passed in 0.48s

python -m pytest -q --basetemp tmp\pytest-audit-20261012-1 <evaluator, graph, boundary modules>
18 passed in 1.11s

python -m pytest -q --basetemp tmp\pytest-audit-20261012-assessment <three selected assessment/repeatability tests>
3 passed in 1.48s

python -m pytest -q --basetemp tmp\pytest-audit-20261012-phase6 <manifest and operator UI modules>
13 passed in 1.51s
```

These overlap and must not be summed as a unique full-suite count. The exact
core regression result is 78 passed. A direct call to
`evaluate_workflow_accuracy("checkout", 1.0)` also verified 4 edge TP, 16 edge
TN, 20 evaluated edges, and `confusion_matrix_over_explicit_universe`.

## Reference workflows and evaluation evidence

The four references are deterministic designed fixtures, not independent
Online Boutique ground truth:

| Workflow | Labelled services | Positive directed edges | Explicit negative edges | Nature |
| --- | ---: | ---: | ---: | --- |
| add_to_cart | 4 | 3 | 9 | Synthetic |
| checkout | 5 | 4 | 16 | Synthetic |
| refund | 4 | 3 | 9 | Synthetic, non-Boutique identities included |
| view_product | 5 | 4 | 16 | Synthetic |

The actual post-change fixture run produced aggregate mandatory-service
TP/FP/FN/TN = 14/0/0/4 and edge-reconstruction TP/FP/FN/TN = 14/0/0/50.
Precision, recall, F1, and edge accuracy are 1.0 on these designed fixtures.
Those values verify deterministic implementation behavior only. They are not
an empirical accuracy claim, do not evaluate validated mandatory edges, and do
not justify a success threshold. The machine-readable per-workflow result is
`experiments/component-1/audit-2026-10-12/evaluation_summary.synthetic.json`.

## Application-independence assessment

| Dimension | Result | Evidence and limit |
| --- | --- | --- |
| Naming independence | Demonstrated in unit scope | Generic identifiers and non-Boutique test services pass; no second application. |
| Workflow independence | Partially demonstrated | Workflows are scenario/operation data, but business success semantics need manual reviewed registration. |
| Structural independence | Partially demonstrated | Chains, branches, repeated calls, missing parents, and optional observations are tested; async links and first-class conditional branches are not. |
| Trace-format independence | Not demonstrated | Core parser consumes Jaeger JSON. A future backend needs a normalizing adapter. |
| Configuration independence | Partially demonstrated | Boutique behavior is isolated in an adapter/profile; several UI/API defaults still use `frontend`. |
| Reference-data independence | Demonstrated in unit scope | Scenario, evidence, and reference files are separate and reference mutation cannot affect production inference. Empirical references remain absent. |

## Execution instructions

The intended local setup remains documented in `ENVIRONMENT.md`. For this checkout:

```text
python -m venv .venv
& '.venv\Scripts\python.exe' -m pip install -r requirements.txt
& '.venv\Scripts\python.exe' scripts/setup_local.py
& '.venv\Scripts\python.exe' -m pytest -v tests
& '.venv\Scripts\python.exe' -m uvicorn app.main:app --reload --loop asyncio
```

This is the intended sequence, but it is currently blocked on Windows at the
requirements-install step. The isolated environment is required because the active interpreter in this
audit did not match the pinned environment. `ENVIRONMENT.md` says uvloop has a
Windows marker, but the current `requirements.txt` contains an unconditional
`uvloop==0.22.1`; that documentation/dependency regression must be resolved by
the shared dependency owner. Until test discovery is configured to ignore
`tmp/`, use `tests` explicitly rather than a bare repository-root invocation.
The application also needs the Kubernetes Python client declared/installed by
the owning component before importing `app.main`; this shared dependency change
requires team review.

Schema validation used in this audit:

```text
python -c "import json; from app.shared.schemas.workflow import WorkflowGraph; WorkflowGraph.model_validate(json.load(open('experiments/component-1/audit-2026-10-12/checkout_workflow_graph.synthetic.json'))['workflowGraph'])"
```

Live integration prerequisites are a reachable Jaeger instance, an
instrumented Online Boutique deployment, a reviewed workflow success contract,
an isolated test namespace, approved intervention controls, and immutable raw
evidence storage. No cluster mutation or fault injection was performed here.

## Real baseline continuation — 2026-10-12

After Docker Desktop became available, the registered Online Boutique checkout
probe was executed against the pinned v0.10.1 Compose deployment. The first
retrieval exposed a real Jaeger indexing/export race: the campaign stopped
polling when any fragment with the expected trace ID appeared, before the
frontend terminal span was present. Campaign polling now waits for the reviewed
terminal service and operation. A regression test covers incremental trace
arrival.

The post-fix real run succeeded and correlated exact trace
`53496a11342b43608ca61906d8345e0d`. The invocation ran from
`2026-10-12T09:46:34.204453Z` to `2026-10-12T09:46:34.819631Z`; the trace's 29
spans fall inside that interval, and the frontend root records
`POST /cart/checkout` with HTTP status 200. The evidence bundle is under
`experiments/component-1/real-baseline-2026-10-12/`.

The trace remains `PARTIAL`. Inspection of the exact upstream v0.10.1 source at
commit `39efed435e697bcdcd31c5e45f4d36dc35c98a63` confirms that shippingservice's
`initTracing` is an empty TODO and cartservice contains no OpenTelemetry setup.
Their environment variables therefore cannot produce server spans. The generic
extractor now recovers observed calls from explicit OpenTelemetry
`server.address`/`net.peer.name` client attributes while refusing IP-only peers
as logical service identities. This recovers checkoutservice-to-cartservice and
checkoutservice-to-shippingservice as observed edges without inferring from
Online Boutique operation names. The quality gate still excludes this partial
trace from primary classification and shared `WorkflowGraph` generation.

Focused post-fix results were 34 passing tests for polling, trace quality, and
interaction extraction; 13 passing outcome-evaluator tests (3 HTTP/API tests
deselected because this environment's `TestClient` path hangs); and 36 passing
trace/interaction/discovery tests. `git diff --check` and Component 1 bytecode
compilation also completed successfully.

## Acceptance criteria

| Criterion | Result | Evidence / blocker |
| --- | --- | --- |
| Actual trace parsing and correlation | PASS for one real run | Exact post-fix checkout trace ID, timing, terminal operation, status, and immutable raw response were checked. Repeated clean-baseline evidence is not yet complete. |
| Correct observed interaction reconstruction | PARTIAL empirically | Ten directed edges and nine services were recovered from the original real trace, including two explicit client-peer edges. Later instrumentation closed the cart/shipping gaps. Span links are now preserved separately, but no real asynchronous workflow has been evaluated. |
| Defensible observed-versus-mandatory distinction | PARTIAL | Candidate and intervention-validated lifecycles are separate and condition-aware; no live intervention campaign has yet established real labels. |
| Independently established references | FAIL empirically | Current references are explicitly synthetic and have no reviewed evidence package. |
| Mandatory/optional/conditional/unknown tests | PASS in software | Mandatory, optional, supporting, conditional, and insufficient-evidence paths are deterministic and tested; empirical labels still require controlled runs. |
| Correct metrics | PASS for implemented universes | TN-aware edge/service accuracy now has an explicit universe and tested zero-safe formulas; live labels are unavailable. |
| Valid `WorkflowGraph` output | PASS | Generated checkout artifact validates against the shared Pydantic schema. |
| Regression testing after fixes | PARTIAL | Prior 78-test core result plus focused 34-, 13-, and 36-test post-fix runs pass; full suite remains blocked by environment/discovery and the `TestClient` hang. |
| Application-specific assumptions documented | PASS | Boutique adapter/defaults and Jaeger coupling are identified above. |
| Demonstrated-versus-future compatibility boundary | PASS | No second-application or universal compatibility claim is made. |

## Supervisor-ready evidence summary

Component 1 now has a defensible separation between observed trace structure,
frequency-based candidates, and intervention-validated dependency labels. Its
core graph reconstruction and metric logic pass deterministic tests, the shared
graph output validates, and edge accuracy now uses an explicit labelled
candidate universe with true negatives. The resulting 1.0 fixture scores are
only software-verification evidence because all current references and traces
used for those scores are synthetic.

The component is suitable for guarded integration when downstream consumers use
approved/frozen graphs rather than raw candidate graphs. It is not yet fully
validated research evidence. The next required experiment is a reproducible,
immutable Online Boutique campaign with reviewed success criteria, exact Jaeger
trace correlation, controlled service and edge interventions, independent
reference review, and post-run metric export. A second independent application
is still required before making broader real-world compatibility claims.

## Instrumented clean baseline continuation — 2026-10-12

The pinned Online Boutique deployment now replaces only cartservice and
shippingservice with locally built, source-pinned images that add the missing
OpenTelemetry server instrumentation. Both builds verify upstream commit
`39efed435e697bcdcd31c5e45f4d36dc35c98a63`. Cartservice uses ASP.NET Core
instrumentation and OTLP/gRPC export; shippingservice uses the Go gRPC
`StatsHandler`, W3C propagation, and OTLP/gRPC export. The Compose override is
`deploy/docker-compose.boutique-instrumented.yml`. Cart's OTLP exporter is
pinned to 1.15.3 rather than the vulnerable 1.15.2-or-earlier range.

With the background load generator stopped for experiment isolation, a fresh
campaign (`campaign:05b1cca886af4f0cad302d4ac806c424`) ran three controlled
checkout trials. All three returned the registered HTTP 200 terminal outcome,
matched their injected trace IDs exactly, and passed trace validation with 33
spans, one root, no broken parents, no unknown services, and no missing service
identities. Campaign readiness reports 3/3 eligible successes, zero exclusions,
no duplicate trace IDs, success rate 1.0, and `ready: true`.

The same nine services and ten directed edges appeared in all three traces, so
every observed edge has frequency 1.0. These are explicitly stored as
frequency candidates, not intervention-validated mandatory dependencies. The
immutable raw Jaeger responses and clean baseline bundle are under
`experiments/component-1/real-baseline-2026-10-12/`.

Independent review of the pinned checkout source found an important semantic
completeness caveat: checkoutservice calls `CurrencyService.Convert` with
`context.TODO()`. That call therefore loses the checkout trace parent, and the
expected checkoutservice-to-currencyservice edge is absent from each otherwise
structurally valid correlated trace. Its absence is not negative evidence. This
distinguishes structural trace validity from semantic workflow completeness and
must remain visible in later metric calculations.

A non-synthetic source-derived reference draft now proposes seven mandatory and
four optional edges, including the missing checkout-to-currency edge. It is
marked `AWAITING_HUMAN_REVIEW`; it must not be used for accuracy claims, graph
approval, or fault-injection conclusions before review. No intervention or
fault injection has been run. The background `boutique-loadgenerator` was
stopped throughout the preserved trials; Docker's later restart reactivated it.
It must be stopped again before any controlled intervention campaign.

## Context propagation and classification-field follow-up — 2026-10-13

Checkoutservice now has a source-pinned compatibility image that changes only
the `CurrencyService.Convert` call from `context.TODO()` to the active request
context. This preserves the upstream business behavior while restoring the
distributed-trace parent. Exact trace `76473a579eed490aa8846025f085fe1a`
completed successfully and, after Jaeger's incremental export settled, contained
37 spans, one root, no broken parents, no unknown or missing service identities,
and all 11 source-expected directed edges. In particular,
checkoutservice-to-currencyservice is now directly observed.

Dependency-assessment API records now expose explicit `mandatoryServices`,
`supportingServices`, and `optionalServices` arrays, plus corresponding grouped
edge fields and insufficient-evidence fields. The workflow UI renders the same
three service groups before approval and on the approved result. Supporting
services remain outside the shared Component 3 `WorkflowGraph` failure gate and
are retained as explicit assessment evidence; the shared cross-component schema
was not changed. Empty supporting or optional arrays are displayed as `None`
rather than being omitted or populated from trace frequency.

## Source-agnostic telemetry onboarding — 2026-10-13

Component 1 now begins with a telemetry-readiness assessment rather than
assuming every project uses OpenTelemetry. The API and workflow UI recognize
live Jaeger, offline Jaeger JSON, Zipkin, Tempo, vendor tracing, correlated
logs, network observations, source/configuration models, and no-data projects.
Results expose an evidence level, adapter availability, blockers, remediation
actions, observed services, and separate candidate-graph and causal-graph gates.

Live Jaeger, offline Jaeger exports, Zipkin v2, and Grafana Tempo are
operational adapter paths. Zipkin v2 spans and Tempo OTLP JSON responses are
normalized into the existing internal trace contract and pass the same
structural quality gate. Generic vendor OTLP JSON is supported as an offline
import; vendor-specific authenticated search is not claimed. Correlated logs,
network observations, and source models now have explicit non-causal edge
normalizers and can support candidate discovery, but never causal parent-child
claims. No-evidence projects remain blocked with actionable next steps.

Telemetry readiness never grants mandatory/supporting/optional validation.
Those labels still require a reviewed success contract, stable correlated
baseline, confirmed interventions, sufficient samples, and human approval. The
operator contract is documented in `docs/component-1/TELEMETRY_ONBOARDING.md`.

HTTP-contract tests cover Zipkin search and Tempo search-plus-exact-trace
retrieval. Normalizer tests cover parent linkage, service identity, timing,
status, quality validation, evidence labels, and rejection of uncorrelated log
records. The Component 1 tests that do not import the whole application pass
159/159. Whole-application collection remains environment-blocked because the
active Python environment lacks the repository's Kubernetes SDK dependency.
The live Jaeger readiness probe passes at evidence level 4 with ten observed
services and no blockers; it correctly keeps validated classification false
until controlled intervention evidence is approved.

The primary guided workflow no longer submits a legacy Jaeger-only URL. It now
persists a typed Jaeger, Zipkin, or Tempo source and uses that source for
operation discovery, evidence inspection, and exact-trace polling. Imported
Jaeger, Zipkin, Tempo/OTLP, correlated-log, network, and source-model JSON can
also be inspected through `/api/workflows/telemetry/inspect`; causal exports
return quality-gated workflows/services/edges while weaker evidence returns
only non-causal candidates.

## Condition-aware evidence and immutable trace snapshots — 2026-10-13

Workflow scenarios can now declare named business conditions such as `guest`
and `returning`. Every execution and service/edge intervention cohort carries a
`conditionId` (legacy records default to `default`). The assessment service
compares baseline and intervention outcomes within the same condition rather
than mixing branches. If adequately evidenced conditions produce different
labels, the aggregate classification is explicitly `conditional` and retains
the complete per-condition decisions.

Conditional results cannot be silently flattened into the shared
`WorkflowGraph`. Global approval is blocked, while
`/api/workflows/dependency-assessments/{assessment_id}/conditions/{condition_id}/graph`
returns an ordinary graph whose meaning is explicitly scoped to that condition.
The shared cross-component schema remains unchanged.

Every cohort statistic now records the server-derived eligible execution IDs
and exact matched trace IDs. In addition, each newly evaluated matched
execution embeds the canonical raw trace used for its outcome together with its
source locator, capture timestamp, and SHA-256 checksum. Repository round trips
recompute and verify that checksum, so later mutation of the saved raw evidence
is detected. This removes the earlier dependency on manually saved trace files
for new execution records; older records remain readable without a snapshot.

The Component 1 tests that do not import the whole application now pass
169/169 using a repository-local pytest temporary directory. Whole-application
collection remains blocked because the active Python environment does not have
the Kubernetes SDK imported by Member 2. No real conditional intervention has
yet been executed, so the new conditional path is software-verified but not an
empirical research result.

Asynchronous OpenTelemetry links and Jaeger `FOLLOWS_FROM` references are now
retained as a separate `linkedEdges` evidence channel. They are deliberately
not merged into synchronous parent-child RPC edges. OTLP normalization carries
span links into the internal representation, and trace-quality validation
recognizes linked secondary roots without treating them as disconnected trace
fragments. This path is software-tested but still needs a real messaging-based
application trace.

Synchronous edge evidence now also records `totalCallCount`,
`maxCallsPerTrace`, and `repeatedTraceCount`. Matching client and server spans
for one remote attempt share a token and are not double-counted. These fields
expose repeated attempts for later retry/fallback analysis; they do not by
themselves decide whether a repeat was a retry, loop, hedge, or application
fan-out.

## Application-independent guided campaigns — 2026-10-13

The guided experiment path no longer enables controlled campaigns by searching
for the word `checkout`, and it no longer injects the Online Boutique benchmark
profile automatically. Every observed workflow can now be configured with
either a reviewed observed-entry HTTP probe or an imported multi-step execution
definition. The operator supplies the workflow name, business outcome, accepted
statuses, optional response marker, application URL, entry service, environment,
and success condition.

Campaign records now preserve `entryService` independently from the terminal
success service. This fixes non-Boutique applications in which an API gateway
starts the workflow but a worker, gRPC service, or downstream span proves the
outcome. `applicationUrl` is now the primary execution input; `frontendUrl`
remains only as a backward-compatible API alias.

The Online Boutique workload driver remains available as an optional benchmark
fixture but is no longer the guided production path. The portability boundary
and required operator inputs are documented in
`docs/component-1/APPLICATION_INDEPENDENCE.md`. A generic order-processing
campaign with `api-gateway` as entry and `order-worker` as terminal service is
covered by round-trip and execution-scenario tests.
