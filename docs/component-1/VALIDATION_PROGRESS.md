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
- Evaluation reports `not_evaluated_no_predicted_edge_labels` until an explicit
  mandatory-edge prediction is supplied, preventing unsupported accuracy
  claims while retaining overall observed-edge and path evaluation.
- Explicit predicted mandatory edges must exist in the generated graph.

### Validated artifact version

Frozen artifacts produced after these corrections use algorithm version
`workflow-dependency-modeling/phase-5-validation-1` so their provenance is
distinguishable from the original Phase 5 behavior.

### Real Jaeger evidence correlation

- A real Online Boutique checkout completed successfully and its injected trace
  ID resolved directly in Jaeger with 56 spans.
- The original campaign fetcher discarded that trace ID and searched a bounded
  service/operation result set, producing a false `missing` correlation while
  the exact evidence existed.
- Campaign trials now retrieve `/api/traces/{trace_id}` evidence directly and
  poll for a bounded ingestion window before declaring it unavailable.
- Outcome timestamps cannot precede the completed workflow invocation.

## Remaining roadmap boundary

Explicit mandatory-edge discovery and classification requires new edge-level
evidence rather than endpoint inference. That belongs in the reproducible
experiment and evidence work planned for Phase 6. Phases 7 and 8 remain the
operational handoff and final empirical study described in the development
plan.
