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
