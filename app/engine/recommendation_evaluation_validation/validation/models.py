"""Types of the post-deployment validation pipeline (Member 4).

Validation answers one question: after a developer-approved change was applied to a TEST environment and a
controlled node failure was injected, did what happened match what the what-if simulation predicted?

Two things are kept apart on purpose and never merged:
* the PREDICTION (``PredictedOutcome``): computed from the virtual model before anything was applied;
* the OBSERVATION: what the test environment actually showed, measured as pod readiness and turned into service and
  workflow outcomes by Component 3's own predictor (nothing here re-implements its survival rules).

The shared ``ValidationReport`` contract (five fields, blast radius only) is not changed; ``to_validation_report``
reduces a result to it.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.shared.schemas.failure_impact import FailureImpactReport


class RunState(str, Enum):
    PLANNED = "PLANNED"
    BASELINE_MEASURED = "BASELINE_MEASURED"
    APPLIED = "APPLIED"
    PLACEMENT_OBSERVED = "PLACEMENT_OBSERVED"
    FAILURE_OBSERVED = "FAILURE_OBSERVED"
    COMPLETED = "COMPLETED"
    # Terminal states without a verdict. None of them can ever be reported as validated.
    INCONCLUSIVE = "INCONCLUSIVE"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


TERMINAL_STATES = frozenset({RunState.COMPLETED, RunState.INCONCLUSIVE, RunState.FAILED, RunState.INTERRUPTED})

# The only environment a validation run may write to or break on purpose.
TEST_ENVIRONMENT = "test"

APPLY, FAIL_NODE, FAIL_BASELINE = "APPLY_TO_TEST_CLUSTER", "FAIL_NODE_IN_TEST_CLUSTER", "FAIL_BASELINE_IN_TEST_CLUSTER"


class ValidationRefusedError(Exception):
    """A step was refused before it changed anything. ``code`` says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


class ValidationInterrupted(Exception):
    """Raised by an executor when the run was cut short (a lost connection, a cancelled job)."""


@dataclass(frozen=True)
class TestTarget:
    """The environment a run is allowed to touch. Anything but ``environment == "test"`` is refused."""

    __test__ = False        # not a pytest class

    name: str
    environment: str


@dataclass(frozen=True)
class Authorization:
    """A person's explicit go-ahead for ONE write or ONE controlled failure.

    It is bound to the exact plan (``plan_digest``: proposal fingerprint, YAML digest, failed node and target) and to
    the action, so an approval for one step, one plan or one cluster cannot be reused for another.
    """

    action: str
    confirmed_by: str
    plan_digest: str
    target: str


@dataclass(frozen=True)
class ValidationThresholds:
    """What counts as agreement. Experimental defaults, configurable; none was tuned on real data."""

    blast_radius_tolerance: float = 5.0        # percentage points, predicted vs observed after the change
    risk_reduction_tolerance: float = 5.0      # percentage points
    require_service_match: bool = True         # the predicted and observed failed-service sets must be equal
    require_workflow_match: bool = True        # the workflow must be predicted to survive/fail as it did


@dataclass(frozen=True)
class PredictedOutcome:
    """The simulation's answer, fixed before anything was applied."""

    recommendation_id: str
    workflow_id: str
    failed_node: str
    fingerprint: str
    before: FailureImpactReport
    after: FailureImpactReport
    # service -> node -> ready replicas, for the services the change moves
    placement: dict[str, dict[str, int]]

    @property
    def risk_reduction(self) -> float:
        return round(self.before.blast_radius - self.after.blast_radius, 2)


@dataclass(frozen=True)
class ObservedOutcome:
    """What one controlled failure (or the derived baseline) showed, as Component 3 reads the observed readiness."""

    report: FailureImpactReport
    # MEASURED: a node was failed and readiness observed. DERIVED_FROM_PLACEMENT: no failure was injected; the
    # predictor was applied to the observed placement, which is a prediction about that placement, not a measurement.
    basis: str


MEASURED, DERIVED = "MEASURED", "DERIVED_FROM_PLACEMENT"


@dataclass(frozen=True)
class ValidationResult:
    recommendation_id: str
    state: RunState
    validated: bool
    reasons: tuple[str, ...]
    predicted: dict[str, Any] = field(default_factory=dict)
    observed: dict[str, Any] = field(default_factory=dict)
    comparison: dict[str, Any] = field(default_factory=dict)
    prediction_error: float | None = None
    needs_manual_restore: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendationId": self.recommendation_id, "state": self.state.value, "validated": self.validated,
            "reasons": list(self.reasons), "predicted": self.predicted, "observed": self.observed,
            "comparison": self.comparison, "predictionError": self.prediction_error,
            "needsManualRestore": self.needs_manual_restore, "isMeasurement": self.state is RunState.COMPLETED,
        }
