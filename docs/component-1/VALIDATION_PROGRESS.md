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

## Remaining validation work

- Correct mandatory-edge semantics instead of deriving them solely from the
  endpoint service labels.
- Clarify set-reconstruction and selective-classification metric names.
- Add the missing path and complete-workflow reconstruction metrics without
  mixing reference truth into prediction.
- Re-run the focused and repository-owned suites after every cohesive change.
