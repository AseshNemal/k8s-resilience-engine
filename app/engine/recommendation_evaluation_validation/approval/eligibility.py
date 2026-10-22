"""Whether a candidate may be put to a developer for approval, judged only from the server's own evaluation.

A candidate is eligible when the evaluation says it is verified feasible AND ranked with a positive score.
``feasible = True`` alone is not enough: a feasible candidate that Phase 2 excluded (no predicted improvement,
risk only moved to another node, missing impact evidence, ...) is shown but cannot be approved.

Where the facts come from. They come from a ``LiveRecommendation``: the report and the evidence that
``live_pipeline`` computed on the server, either when the approval was requested or afresh when it is decided.
Nothing a client sends (a rank, a score, a selection, a justification field) is read here. Anything that is
missing, malformed or inconsistent makes the candidate ineligible; there is no fallback to the report's score
alone, because a score without the ranking behind it proves nothing.

The top-ranked candidate is the automatic selection. Another ranked candidate is eligible too, but approving
it needs a written justification (see ``ApprovalService.approve``).
"""

import math
from dataclasses import dataclass
from typing import Any

from .models import LiveRecommendation

# The report rounds its score to four decimals; the ranking carries the unrounded value.
SCORE_TOLERANCE = 1e-3


@dataclass(frozen=True)
class Standing:
    """A candidate's place in an evaluation: whether it may be approved, and whether it is the selection."""

    eligible: bool
    reason: str | None = None
    rank: int | None = None
    score: float | None = None
    selected_recommendation_id: str | None = None
    is_selected: bool = False

    @property
    def requires_justification(self) -> bool:
        return self.eligible and not self.is_selected

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "score": self.score,
            "selectedRecommendationId": self.selected_recommendation_id,
            "isSelected": self.is_selected,
            "requiresJustification": self.requires_justification,
        }


def _ineligible(reason: str) -> Standing:
    return Standing(eligible=False, reason=reason)


def _is_rank(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def evaluate_eligibility(live: LiveRecommendation, recommendation_id: str) -> Standing:
    report = live.report
    candidate = next((c for c in report.recommendations if c.id == recommendation_id), None)
    if candidate is None:
        return _ineligible(f"Candidate {recommendation_id} is not in the current evaluation")
    if not candidate.feasible:
        return _ineligible(f"Candidate {recommendation_id} is not feasible")

    ranking = live.evidence.get("ranking") if isinstance(live.evidence, dict) else None
    entries = ranking.get("candidates") if isinstance(ranking, dict) else None
    entry = entries.get(recommendation_id) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return _ineligible(f"Candidate {recommendation_id} has no authoritative ranking in the current evaluation, "
                           f"so it cannot be approved")

    rank, score, excluded = entry.get("rank"), entry.get("score"), entry.get("excluded")
    if excluded or not _is_rank(rank):
        return _ineligible(f"Candidate {recommendation_id} is not ranked ({excluded or 'no rank'}); only a ranked "
                           f"candidate can be approved")
    if not _is_positive_number(score) or candidate.score <= 0:
        return _ineligible(f"Candidate {recommendation_id} has no positive ranking score")
    if abs(candidate.score - score) > SCORE_TOLERANCE:
        return _ineligible(f"Candidate {recommendation_id}: the report and the ranking disagree about its score")

    # Counted loosely on purpose (True == 1): a malformed entry anywhere makes the evaluation inconsistent.
    top = [name for name, other in entries.items() if isinstance(other, dict) and other.get("rank") == 1]
    selected = report.selected_recommendation_id
    if len(top) != 1 or selected != top[0]:
        return _ineligible("The evaluation is inconsistent about which candidate is top-ranked, so nothing in it "
                           "can be approved")

    return Standing(eligible=True, rank=rank, score=float(score), selected_recommendation_id=selected,
                    is_selected=recommendation_id == selected)
