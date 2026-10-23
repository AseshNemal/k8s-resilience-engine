"""The post-change failure-impact analysis of an applied (or reset) change.

When a rollout completes, the workflow's failure impact is recalculated from the freshly observed topology —
by Member 3's predictor, called through the tier's own evidence path, never recomputed here — and compared
with the two numbers that were fixed before the change:

* BEFORE: the blast radius of the topology the recommendation was generated from (immutable: stored when the
  change was applied, and never replaced by a later evaluation);
* PREDICTED: the approved proposal's what-if result (for a reset: the original baseline again, the state the
  reset restores);
* AFTER: the recalculated blast radius from the post-rollout topology.

What this is, and what it is not: the AFTER value is observed pod placement plus model recalculation. It is
NOT an experimentally observed workflow failure rate — no failure was injected, no workflow was measured.
``RESULT_LABEL`` says exactly that and is stored on every row and returned in every response, so no consumer
has to invent the wording. Genuine controlled-experiment evidence belongs to the validation subsystem, which
this never speaks for.

The comparison is refused (status UNAVAILABLE, with the reason) rather than guessed when its basis moved: the
connected cluster is not the one the change was applied to, the frozen workflow was re-frozen, or the declared
workflow was edited since. A transient failure (the cluster cannot be read right now) is status FAILED and can
be retried through the explicit retry action; a retry never reapplies or re-resets anything.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.evidence_errors import EvidenceError
from app.shared.persistence.models import AppliedChangeAnalysisRecord

COMPONENT = "recommendation"

PENDING = "PENDING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"              # the recalculation could not run now (for example the cluster cannot be read); retryable
UNAVAILABLE = "UNAVAILABLE"    # the comparison basis moved, so a comparison would mislead; terminal, explained

RESULT_LABEL = ("Post-change risk recalculated from live topology. It is a model recalculation over observed pod "
                "placement, not an experimentally observed workflow failure rate, and not a controlled-experiment "
                "validation.")
PREDICTED_MEANING = "The approved proposal's what-if result (virtual simulation), fixed when the change was applied."
RESET_PREDICTED_MEANING = "The pre-change baseline, which the reset restores; the restoration target, not a new prediction."

DIRECTION_APPLY = "apply"
DIRECTION_RESET = "reset"


class AnalysisUnavailable(EvidenceError):
    """The comparison cannot be made because its basis moved (cluster, workflow freeze or declaration). Terminal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, http_status=409, component=COMPONENT, retryable=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ChangeAnalysis:
    analysis_id: str
    application_id: str
    recommendation_id: str
    direction: str
    status: str
    basis: dict[str, Any]
    baseline: dict[str, Any]
    label: str
    predicted: dict[str, Any] | None = None
    observed: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysisId": self.analysis_id, "applicationId": self.application_id,
            "recommendationId": self.recommendation_id, "direction": self.direction, "status": self.status,
            "basis": self.basis, "baseline": self.baseline, "predicted": self.predicted,
            "observed": self.observed, "comparison": self.comparison, "error": self.error, "label": self.label,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
        }


class ChangeAnalyser(Protocol):
    """How one tier plans and runs the recalculation. Implemented in ``live_pipeline`` per tier (Member 4-owned)."""

    def plan(self, change: Any, live: Any) -> dict[str, Any]:
        """The immutable basis, baseline and prediction for an apply, from the evaluation the apply re-verified."""

    def recalculate(self, basis: dict[str, Any]) -> dict[str, Any]:
        """Fresh topology plus Member 3's prediction. Raises AnalysisUnavailable (basis moved) or a coded EvidenceError."""


def plan_reset(prior: ChangeAnalysis) -> dict[str, Any]:
    """The plan for a reset: the apply's basis and its immutable baseline; the restoration target is that baseline itself.

    A reset has no prediction of its own. What it should produce is the state before the change, so that is what the
    recalculation is compared with, and it is labelled as a restoration target, not as a prediction.
    """
    return {"basis": prior.basis, "baseline": prior.baseline,
            "predicted": {**prior.baseline, "kind": "restoration-target"}}


def compare(baseline: dict[str, Any], predicted: dict[str, Any] | None, observed: dict[str, Any]) -> dict[str, Any]:
    """The three-way comparison, in percentage points. Unknown stays unknown; no unknown is ever a zero."""
    before, after = baseline.get("blastRadius"), observed.get("blastRadius")
    result: dict[str, Any] = {
        "beforeBlastRadius": before, "afterBlastRadius": after,
        "reductionPoints": None, "direction": None, "relativeReductionPercent": None,
        "predictedBlastRadius": None, "predictionDeltaPoints": None,
    }
    if before is None or after is None:
        return result
    delta = round(before - after, 2)
    result["reductionPoints"] = delta
    result["direction"] = "improved" if delta > 0 else "worsened" if delta < 0 else "unchanged"
    if before > 0:      # a relative figure against a zero baseline is meaningless; it stays unknown, not infinite
        result["relativeReductionPercent"] = round(delta / before * 100, 1)
    predicted_radius = (predicted or {}).get("blastRadius")
    if predicted_radius is not None:
        result["predictedBlastRadius"] = predicted_radius
        result["predictionDeltaPoints"] = round(after - predicted_radius, 2)
    return result


class ChangeAnalysisRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    _FIELDS = ("analysis_id", "application_id", "recommendation_id", "direction", "status", "basis", "baseline",
               "predicted", "observed", "comparison", "error", "label", "created_at", "completed_at")

    @classmethod
    def _domain(cls, row: AppliedChangeAnalysisRecord) -> ChangeAnalysis:
        return ChangeAnalysis(id=row.id, **{name: getattr(row, name) for name in cls._FIELDS})

    def save(self, analysis: ChangeAnalysis) -> ChangeAnalysis:
        row = AppliedChangeAnalysisRecord(**{name: getattr(analysis, name) for name in self._FIELDS})
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return self._domain(row)

    def update(self, analysis: ChangeAnalysis) -> ChangeAnalysis:
        row = self.session.scalar(select(AppliedChangeAnalysisRecord)
                                  .where(AppliedChangeAnalysisRecord.analysis_id == analysis.analysis_id))
        if row is None:
            raise LookupError(analysis.analysis_id)
        for name in self._FIELDS:
            setattr(row, name, getattr(analysis, name))
        self.session.commit()
        self.session.refresh(row)
        return self._domain(row)

    def latest_for(self, application_id: str, direction: str) -> ChangeAnalysis | None:
        row = self.session.scalar(select(AppliedChangeAnalysisRecord)
                                  .where(AppliedChangeAnalysisRecord.application_id == application_id,
                                         AppliedChangeAnalysisRecord.direction == direction)
                                  .order_by(AppliedChangeAnalysisRecord.id.desc()))
        return None if row is None else self._domain(row)

    def planned(self, change: Any, planned: dict[str, Any], direction: str) -> ChangeAnalysis:
        """The PENDING row written when a direction's patches were accepted, with its immutable baseline."""
        return self.save(ChangeAnalysis(
            analysis_id=f"analysis-{uuid.uuid4().hex[:16]}", application_id=change.application_id,
            recommendation_id=change.recommendation_id, direction=direction, status=PENDING,
            basis=planned["basis"], baseline=planned["baseline"], predicted=planned.get("predicted"),
            label=RESULT_LABEL, created_at=_now()))
