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

## Remaining validation work

- Correct mandatory-edge semantics instead of deriving them solely from the
  endpoint service labels.
- Clarify selective-classification accuracy in exported evaluation results.
- Re-run the focused and repository-owned suites after every cohesive change.
