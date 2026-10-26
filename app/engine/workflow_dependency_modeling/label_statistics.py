"""How certain each dependency label is, computed from the run counts it was decided on.

The decision rule (``dependency_assessor.classify_cohort_effect``) compares point estimates with thresholds. With a
handful of runs a point estimate can cross a threshold by chance, so this module reports, for the same counts:

- a Wilson score interval for each success rate (Wilson, 1927);
- a Newcombe hybrid-score interval for the effect, baseline rate minus outage rate (Newcombe, 1998, method 10);
- a one-sided Fisher exact test of "the outage lowered the success rate";
- whether the label still holds when the threshold comparison uses the interval instead of the point estimate, and
  if not, how many runs per cohort would make it hold if the observed rates stayed the same.

Nothing here changes a label. It qualifies it, from evidence the assessment already holds, so it applies equally to
assessments stored before it existed. The confidence level is the backend's setting, reported with every result.
"""

from __future__ import annotations

import os
from math import comb, sqrt
from statistics import NormalDist
from typing import Any

CONFIDENCE_SETTING = "WORKFLOW_LABEL_CONFIDENCE_LEVEL"
_DEFAULT_CONFIDENCE = 0.95
_MAX_RUNS_SEARCHED = 200


def confidence_level() -> float:
    raw = (os.environ.get(CONFIDENCE_SETTING) or "").strip()
    try:
        level = float(raw) if raw else _DEFAULT_CONFIDENCE
    except ValueError as err:
        raise ValueError(f"{CONFIDENCE_SETTING} must be a number such as 0.95") from err
    if not 0.5 <= level < 1:
        raise ValueError(f"{CONFIDENCE_SETTING} must be at least 0.5 and below 1")
    return level


def _z(level: float) -> float:
    """Two-sided normal quantile: 1.96 for 95%."""
    return NormalDist().inv_cdf(1 - (1 - level) / 2)


def wilson_interval(successes: int, n: int, level: float) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion; ``None`` without observations.

    With p = successes / n and z from the confidence level:

        centre = (p + z²/2n) / (1 + z²/n)
        half   = z·sqrt(p(1−p)/n + z²/4n²) / (1 + z²/n)

    Unlike p ± z·sqrt(p(1−p)/n), it stays inside [0, 1] and is honest at 0/n and n/n, which is exactly where
    outage cohorts land. Example: 5/5 at 95% gives [0.566, 1.000]; 0/5 gives [0.000, 0.434].
    """
    if n <= 0:
        return None
    # z = 1.96 at 95% confidence (how many standard errors the interval spans)
    z = _z(level)
    # p = observed success rate, e.g. 5/5 = 1.0
    p = successes / n
    # Wilson centre: p pulled towards 0.5 by z²/2n; the pull shrinks as n grows (few runs → more caution)
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    # Wilson half-width: stays above zero even when p is 0 or 1, so 5/5 is not reported as "certainly 100%"
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    # Interval = centre ± half, clipped to [0, 1]. 5/5 → [0.566, 1.000]; 0/5 → [0.000, 0.434]
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe_interval(s1: int, n1: int, s2: int, n2: int, level: float) -> tuple[float, float] | None:
    """Interval for p1 − p2 from the two Wilson intervals (Newcombe's hybrid score method).

        lower = (p1 − p2) − sqrt((p1 − l1)² + (u2 − p2)²)
        upper = (p1 − p2) + sqrt((u1 − p1)² + (p2 − l2)²)

    where [l1, u1] and [l2, u2] are the Wilson intervals of the baseline and outage rates. This is the range
    of the effect Δ the decision rule compares with its thresholds. Example: 5/5 vs 0/5 → [0.386, 1.000].
    """
    first, second = wilson_interval(s1, n1, level), wilson_interval(s2, n2, level)
    if first is None or second is None:
        return None
    # p1 = normal success rate, p2 = outage success rate
    p1, p2 = s1 / n1, s2 / n2
    (l1, u1), (l2, u2) = first, second
    # Point estimate of the effect Δ = p1 − p2 (the value the decision rule uses)
    difference = p1 - p2
    # Lowest plausible Δ: p1 could be as low as l1 and p2 as high as u2; their distances combine in quadrature
    lower = difference - sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    # Highest plausible Δ: p1 as high as u1, p2 as low as l2. 5/5 vs 0/5 → Δ in [0.386, 1.000]
    upper = difference + sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return max(-1.0, lower), min(1.0, upper)


def fisher_one_sided(s1: int, n1: int, s2: int, n2: int) -> float | None:
    """P(at least this many baseline successes | margins): small when the outage really lowered success.

    Fisher's exact test on the 2×2 table (baseline/outage × succeeded/failed), one-sided. With the total
    number of successes fixed, the baseline's share follows a hypergeometric distribution:

        p = Σ_{k ≥ s1} C(n1, k)·C(n2, S − k) / C(n1 + n2, S),   S = s1 + s2

    Example: 5/5 vs 0/5 → only the observed table is that extreme → p = 1/C(10,5) = 1/252 ≈ 0.004.
    """
    if n1 <= 0 or n2 <= 0:
        return None
    # S = all successes, N = all runs. If the outage changed nothing, which runs succeeded would be random.
    successes, total = s1 + s2, n1 + n2
    # C(N, S) = number of ways to place the S successes among all N runs
    denominator = comb(total, successes)
    # p = share of those placements giving the normal cohort at least s1 successes (as many as observed or more).
    # Small p: such a split is unlikely by chance, so the outage really lowered success. 5/5 vs 0/5 → 1/252.
    return sum(
        comb(n1, k) * comb(n2, successes - k)
        for k in range(s1, min(n1, successes) + 1)
        if 0 <= successes - k <= n2
    ) / denominator


def _supported(label: str, s1: int, n1: int, s2: int, n2: int, policy: Any, level: float) -> tuple[bool, list[str]] | None:
    """Does the label still hold when its thresholds are checked against the interval, not the point value?

    mandatory:  the whole effect interval is at or above the mandatory threshold, and the outage success
                rate's upper bound is at most the allowed maximum;
    optional:   the whole effect interval lies within ±tolerance;
    supporting: the whole effect interval lies between tolerance and the mandatory threshold.
    """
    effect = newcombe_interval(s1, n1, s2, n2, level)
    outage = wilson_interval(s2, n2, level)
    if effect is None or outage is None:
        return None
    # The same thresholds as the decision rule, applied to the whole interval instead of the single value Δ
    lower, upper = effect
    # Mandatory holds if even the lowest plausible Δ reaches 0.5 and even the highest plausible outage success
    # rate stays at or below 0.2.
    if label == "mandatory":
        checks = [
            (lower >= policy.mandatory_effect_threshold,
             f"effect lower bound {lower:.3f} ≥ {policy.mandatory_effect_threshold}"),
            (outage[1] <= policy.max_mandatory_success_rate,
             f"outage success-rate upper bound {outage[1]:.3f} ≤ {policy.max_mandatory_success_rate}"),
        ]
    # Optional holds if every plausible Δ is within ±0.1 (the whole interval inside the tolerance).
    elif label == "optional":
        checks = [(lower >= -policy.optional_effect_tolerance and upper <= policy.optional_effect_tolerance,
                   f"effect interval [{lower:.3f}, {upper:.3f}] within ±{policy.optional_effect_tolerance}")]
    # Supporting holds if every plausible Δ is above the tolerance and below the mandatory threshold.
    elif label == "supporting":
        checks = [(lower > policy.optional_effect_tolerance and upper < policy.mandatory_effect_threshold,
                   f"effect interval [{lower:.3f}, {upper:.3f}] inside ({policy.optional_effect_tolerance}, "
                   f"{policy.mandatory_effect_threshold})")]
    else:
        return None
    return all(ok for ok, _ in checks), [f"{text} → {'yes' if ok else 'no'}" for ok, text in checks]


def _runs_needed(label: str, s1: int, n1: int, s2: int, n2: int, policy: Any, level: float) -> int | None:
    """Smallest equal cohort size at which the label would be supported, if the observed rates held.

    Intervals narrow as n grows, so this searches n upward with the same success proportions. Example: 5/5 vs
    0/5 needs n = 16 for mandatory, because 0/n has upper bound z²/(n + z²) ≤ 0.2 only from n ≈ 15.4.
    """
    p1, p2 = s1 / n1, s2 / n2
    # Try larger equal cohorts with the same success proportions; intervals narrow as n grows, so the first
    # n at which the label holds is the number of runs per cohort needed (5/5 vs 0/5 → 16 for mandatory).
    for n in range(max(n1, n2), _MAX_RUNS_SEARCHED + 1):
        result = _supported(label, round(p1 * n), n, round(p2 * n), n, policy, level)
        if result is not None and result[0]:
            return n
    return None


def label_statistics(label: str, baseline: Any, outage: Any, policy: Any) -> dict[str, Any]:
    """Intervals, test and support verdict for one label, from its two cohorts' eligible counts."""
    level = confidence_level()
    s1, n1 = baseline.succeeded_records, baseline.eligible_records
    s2, n2 = outage.succeeded_records, outage.eligible_records
    effect = newcombe_interval(s1, n1, s2, n2, level)
    supported = _supported(label, s1, n1, s2, n2, policy, level)
    result: dict[str, Any] = {
        "confidenceLevel": level,
        "baselineRateInterval": list(wilson_interval(s1, n1, level) or []) or None,
        "outageRateInterval": list(wilson_interval(s2, n2, level) or []) or None,
        "effectInterval": list(effect) if effect else None,
        "fisherOneSidedP": fisher_one_sided(s1, n1, s2, n2),
        "methods": "Wilson score intervals; Newcombe hybrid-score interval for the difference; one-sided Fisher exact test.",
    }
    if supported is None:
        result.update(supportedAtConfidence=None, checks=[], runsNeededPerCohort=None,
                      verdict="No interval check applies to this label.")
        return result
    ok, checks = supported
    needed = None if ok else _runs_needed(label, s1, n1, s2, n2, policy, level)
    result.update(
        supportedAtConfidence=ok,
        checks=checks,
        runsNeededPerCohort=needed,
        verdict=(f"{label} holds at {level:.0%} confidence" if ok else
                 f"{label} is the point-estimate result but is not yet supported at {level:.0%} confidence"
                 + (f"; about {needed} eligible runs per cohort would support it if the rates stay the same" if needed
                    else "; more runs alone would not support it at these rates")),
    )
    return result
