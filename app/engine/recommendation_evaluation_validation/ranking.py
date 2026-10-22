"""Ranking of evaluated recommendation candidates (Member 4, Phase 2).

A pure, deterministic calculation over data that already exists: each candidate's feasibility verdict and
placement churn (Phase 1) and Component 3's before and after impact for the same assumed node failure. It
predicts nothing, reads no cluster and applies nothing; selecting the top candidate is not approving it.

Formula (version ``FORMULA_VERSION``)::

    target_reduction  t = (blast_before[X] - blast_after[X]) / blast_before[X]      X = the assessed node
    cluster_reduction s = (mean_n blast_before - mean_n blast_after) / mean_n blast_before
    benefit           B = alpha * t + (1 - alpha) * s
    placement_churn   D = len(pods_to_recreate) / ready_replicas
    resource_fraction R = {LOW: 0, MEDIUM: 0.5, HIGH: 1}[resource_cost]
    score = confidence * B * (1 - d * D) * (1 - r * R)

Provisional weights ``alpha = 0.7, d = 0.3, r = 0.3`` are experimental defaults. They are neither optimised
nor proven; the sensitivity tests only show how far a ranking moves when they do.

Normalisation. Every term is already a proportion in [0, 1] (a percentage divided by 100, or a ratio of
counts), so nothing is rescaled against the other candidates. A candidate's score therefore never depends on
which others exist, and adding or removing one cannot reorder the rest. Min-max scaling would break both, and
is undefined for one candidate or for a constant metric. Reductions are *relative* (the share of existing
risk removed), so they do not shrink as the cluster grows.

Correlated metrics. Component 3 defines ``survivability = 100 - blast_radius`` exactly, so they are one
signal: survivability is carried for display and never scored. ``t`` and ``s`` overlap (the assessed node is
one of the nodes ``s`` averages over), which is why ``alpha`` is a single mix and ``s`` also serves as the
guard against moving risk elsewhere. Concentration reduction tracks ``t`` and is not scored. Churn is nearly
independent of benefit. In practice churn and resource cost mostly decide ties on benefit.

Placement churn is not rollout cost. ``D`` measures how many ready replicas have a different target node than
today. Applying a spread constraint means editing the Deployment's pod template, which triggers a rollout that
eventually replaces every pod of that Deployment. The real operational cost (rollout scope, surge capacity,
the RollingUpdate transient) is a separate future requirement and is not modelled or simulated here.

Confidence is evidence coverage, the share of the service's declared replicas whose placement was observed.
It is not a measure of prediction accuracy, and nothing here validates the prediction.

Eligibility. Only ``FEASIBLE`` candidates are ranked. Every other candidate stays in the output, unranked,
with the reason. A feasible candidate is also excluded when Component 3's impact evidence is missing or not a
finite proportion, when its resource cost is not one of the known labels (it is not given a guessed penalty),
when there is no baseline risk to reduce, when it makes the assessed node or the worst-case node riskier, or
when it only moves risk (the assessed node improves but neither the cluster average nor the worst case does).

Modes. A snapshot carries its ``basis``: ``single-workflow`` today. A portfolio basis can be added from
Component 3's portfolio report without a new predictor: ``portfolio-equal-weight`` uses its blast radius and
per-node blast radius as they are, and ``portfolio-criticality-weighted`` re-weights the per-workflow outcomes
it already reports by each workflow's criticality. The two answer different questions and are never mixed:
candidates ranked together must share one basis and one assessed failure.

Known limitations.
* Each candidate is scored on its own. Two changes that only work together (one service rescues nothing until
  a second is also spread) are invisible to this ranking; combined evaluation is outside this phase.
* In single-workflow mode the ranking never had a contested choice in the generated scenarios: of 343
  generated clusters (400 fixed random seeds) that had at least one candidate, none had two rankable ones (one
  workflow needs every failed service fixed at once). The weights can therefore not change a single-workflow
  outcome in those scenarios; they matter in a portfolio mode (on which the sensitivity tests are run) or once
  other strategies exist. These scenarios are synthetic and seeded. They are not observations of, or
  experiments on, any real cluster, and say nothing about how often a real deployment would have two rankable
  candidates.
* The weights are provisional. Sensitivity testing on those generated scenarios shows the ranking is stable
  under moderate changes and that ``disruption_discount = 0.3`` is the most central discount; it does not
  distinguish ``target_share`` values between 0.5 and 0.9, so 0.7 is a design choice, not a measurement.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from app.engine.recommendation_evaluation_validation.feasibility_analyzer import FeasibilityStatus
from app.engine.recommendation_evaluation_validation.simulation_result import SimulationResult

FORMULA_VERSION = "1"
WEIGHTS_STATUS = "experimental defaults; not optimized or proven"
DEFAULT_TOLERANCE = 1e-6
SINGLE_WORKFLOW = "single-workflow"
# Ordinal cost labels. A candidate type adds real cost only when it adds replicas (spread adds none).
RESOURCE_FRACTION = MappingProxyType({"LOW": 0.0, "MEDIUM": 0.5, "HIGH": 1.0})
# How restrictive a strategy is, for breaking a tie between candidates that score the same, cost the same and move the
# same pods: the least restrictive wins (the "minimal change"). A plain spread tolerates a skew of one; required
# anti-affinity forbids a second replica on a node and leaves replicas Pending when nodes run out. A type that is not
# listed sorts after every listed one, so it can never silently beat a known strategy.
RESTRICTIVENESS = MappingProxyType({"TOPOLOGY_SPREAD": 0, "COMBINATION": 0, "POD_ANTI_AFFINITY": 1, "REPLICA_INCREASE": 2})
# Component 3 reports blast radius and survivability as percentages; the two views of one node must agree.
_PERCENT_TOLERANCE = 0.01


class ExclusionReason(str, Enum):
    INFEASIBLE = "INFEASIBLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID_INPUT = "INVALID_INPUT"
    UNKNOWN_RESOURCE_COST = "UNKNOWN_RESOURCE_COST"
    MISSING_IMPACT_EVIDENCE = "MISSING_IMPACT_EVIDENCE"
    INVALID_IMPACT_DATA = "INVALID_IMPACT_DATA"
    NO_BASELINE_RISK = "NO_BASELINE_RISK"
    WORSENS_ASSESSED_NODE = "WORSENS_ASSESSED_NODE"
    WORSENS_WORST_CASE_NODE = "WORSENS_WORST_CASE_NODE"
    RISK_TRANSFER_ONLY = "RISK_TRANSFER_ONLY"
    NO_PREDICTED_IMPROVEMENT = "NO_PREDICTED_IMPROVEMENT"


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _proportion(value: Any) -> bool:
    return _finite(value) and 0.0 <= value <= 1.0


@dataclass(frozen=True)
class RankingWeights:
    target_share: float = 0.7           # alpha: assessed node versus cluster average
    disruption_discount: float = 0.3    # d: most of the score placement churn can remove
    resource_discount: float = 0.3      # r: most of the score resource cost can remove

    def __post_init__(self) -> None:
        for name in ("target_share", "disruption_discount", "resource_discount"):
            if not _proportion(getattr(self, name)):
                raise ValueError(f"{name} must be a finite number between 0 and 1, got {getattr(self, name)!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"targetShare": self.target_share, "disruptionDiscount": self.disruption_discount,
                "resourceDiscount": self.resource_discount, "status": WEIGHTS_STATUS}


@dataclass(frozen=True)
class ImpactSnapshot:
    """Component 3's result for one assumed node failure, reduced to what ranking needs."""

    failed_node: str
    blast_radius: float                 # percent, 0..100, at the assessed node
    survivability: float                # percent; display only (100 - blast_radius)
    node_blast: Mapping[str, float]     # percent for every worker node, the assessed one included
    basis: str = SINGLE_WORKFLOW


@dataclass(frozen=True)
class EvaluatedCandidate:
    id: str
    type: str
    service: str
    simulation: SimulationResult        # carries the feasibility status and pods_to_recreate
    before: ImpactSnapshot | None
    after: ImpactSnapshot | None        # Component 3 on the simulated deployment; None unless FEASIBLE
    ready_replicas: int
    confidence: float                   # evidence coverage, 0..1
    resource_cost: str                  # LOW | MEDIUM | HIGH


@dataclass(frozen=True)
class ScoreComponents:
    target_reduction: float
    cluster_reduction: float
    worst_case_reduction: float         # reported and used to spot risk transfer; not in the score
    benefit: float
    placement_churn: float
    resource_fraction: float
    confidence: float
    churn_factor: float
    resource_factor: float
    blast_before: float
    blast_after: float
    mean_blast_before: float
    mean_blast_after: float
    worst_case_before: float
    worst_case_after: float
    survivability_before: float
    survivability_after: float
    basis: str
    failed_node: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "targetReduction": self.target_reduction, "clusterReduction": self.cluster_reduction,
            "worstCaseReduction": self.worst_case_reduction, "benefit": self.benefit,
            "placementChurn": self.placement_churn, "resourceFraction": self.resource_fraction,
            "confidence": self.confidence, "churnFactor": self.churn_factor, "resourceFactor": self.resource_factor,
            "blastBefore": self.blast_before, "blastAfter": self.blast_after,
            "meanBlastBefore": self.mean_blast_before, "meanBlastAfter": self.mean_blast_after,
            "worstCaseBefore": self.worst_case_before, "worstCaseAfter": self.worst_case_after,
            "survivabilityBefore": self.survivability_before, "survivabilityAfter": self.survivability_after,
            "basis": self.basis, "failedNode": self.failed_node,
        }


@dataclass(frozen=True)
class RankedCandidate:
    id: str
    type: str
    service: str
    rank: int | None
    score: float | None
    components: ScoreComponents | None
    excluded: ExclusionReason | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "type": self.type, "service": self.service, "rank": self.rank, "score": self.score,
            "excluded": self.excluded.value if self.excluded else None, "detail": self.detail,
            "components": self.components.to_dict() if self.components else None,
        }


def _snapshot_problem(snapshot: ImpactSnapshot) -> str | None:
    """Why the snapshot cannot be used, or None."""
    if not isinstance(snapshot, ImpactSnapshot) or not snapshot.node_blast:
        return "no per-node blast radius"
    values = [snapshot.blast_radius, snapshot.survivability, *snapshot.node_blast.values()]
    if not all(_finite(v) and 0.0 <= v <= 100.0 for v in values):
        return "a blast radius or survivability is not a finite percentage between 0 and 100"
    if snapshot.failed_node not in snapshot.node_blast:
        return f"the assessed node {snapshot.failed_node} is not among the nodes reported"
    if abs(snapshot.node_blast[snapshot.failed_node] - snapshot.blast_radius) > _PERCENT_TOLERANCE:
        return "the assessed node's blast radius disagrees with the per-node figures"
    return None


def impact_reductions(before: ImpactSnapshot | None, after: ImpactSnapshot | None,
                      tolerance: float = DEFAULT_TOLERANCE) -> tuple[float, float] | None:
    """(target_reduction, cluster_reduction) between two snapshots, as ``rank_candidates`` computes them.

    None when either snapshot is missing or unusable, when they do not describe the same failure, basis and
    nodes, or when there is no baseline risk to reduce. Used to tell whether a combination's members each matter.
    """
    if not isinstance(before, ImpactSnapshot) or not isinstance(after, ImpactSnapshot):
        return None
    if _snapshot_problem(before) or _snapshot_problem(after):
        return None
    if (before.failed_node, before.basis) != (after.failed_node, after.basis) or set(before.node_blast) != set(after.node_blast):
        return None
    mean_before = sum(before.node_blast.values()) / len(before.node_blast)
    mean_after = sum(after.node_blast.values()) / len(after.node_blast)
    if before.blast_radius <= tolerance or mean_before <= tolerance:
        return None
    return ((before.blast_radius - after.blast_radius) / before.blast_radius,
            (mean_before - mean_after) / mean_before)


def _excluded(candidate: EvaluatedCandidate, reason: ExclusionReason, detail: str,
              components: ScoreComponents | None = None) -> RankedCandidate:
    return RankedCandidate(candidate.id, candidate.type, candidate.service, None, None, components, reason, detail)


def _score(candidate: EvaluatedCandidate, weights: RankingWeights, tolerance: float) -> RankedCandidate:
    """The score of one candidate, or the reason it cannot be ranked. Never looks at another candidate."""
    status = getattr(candidate.simulation, "feasibility_status", None)
    if status is FeasibilityStatus.INFEASIBLE:
        return _excluded(candidate, ExclusionReason.INFEASIBLE, "not feasible: a verified constraint prevents it")
    if status is not FeasibilityStatus.FEASIBLE or candidate.simulation.feasible is not True:
        return _excluded(candidate, ExclusionReason.INSUFFICIENT_EVIDENCE,
                         "feasibility is not verified, so the candidate is not ranked")

    replicas, churned = candidate.ready_replicas, len(candidate.simulation.pods_to_recreate)
    if not (_finite(replicas) and replicas >= 1 and churned <= replicas):
        return _excluded(candidate, ExclusionReason.INVALID_INPUT, f"ready replica count {replicas!r} is not usable")
    if not _proportion(candidate.confidence):
        return _excluded(candidate, ExclusionReason.INVALID_INPUT,
                         f"confidence {candidate.confidence!r} is not a proportion between 0 and 1")
    if not isinstance(candidate.resource_cost, str) or candidate.resource_cost not in RESOURCE_FRACTION:
        return _excluded(candidate, ExclusionReason.UNKNOWN_RESOURCE_COST,
                         f"resource cost {candidate.resource_cost!r} is not one of {sorted(RESOURCE_FRACTION)}; "
                         f"no penalty is guessed for it")
    if candidate.before is None or candidate.after is None:
        return _excluded(candidate, ExclusionReason.MISSING_IMPACT_EVIDENCE,
                         "Component 3's before or after impact is missing")
    for label, snapshot in (("before", candidate.before), ("after", candidate.after)):
        problem = _snapshot_problem(snapshot)
        if problem:
            return _excluded(candidate, ExclusionReason.INVALID_IMPACT_DATA, f"{label} impact: {problem}")
    before, after = candidate.before, candidate.after
    if (before.failed_node, before.basis) != (after.failed_node, after.basis) or set(before.node_blast) != set(after.node_blast):
        return _excluded(candidate, ExclusionReason.INVALID_IMPACT_DATA,
                         "before and after describe different failures, bases or node sets")

    mean_before = sum(before.node_blast.values()) / len(before.node_blast)
    mean_after = sum(after.node_blast.values()) / len(after.node_blast)
    worst_before, worst_after = max(before.node_blast.values()), max(after.node_blast.values())
    if before.blast_radius <= tolerance or mean_before <= tolerance:
        return _excluded(candidate, ExclusionReason.NO_BASELINE_RISK,
                         "no risk at the assessed node to reduce, so there is nothing to recommend")

    target = (before.blast_radius - after.blast_radius) / before.blast_radius
    cluster = (mean_before - mean_after) / mean_before
    worst_case = (worst_before - worst_after) / worst_before
    churn = churned / replicas
    components = ScoreComponents(
        target_reduction=target, cluster_reduction=cluster, worst_case_reduction=worst_case,
        benefit=weights.target_share * max(target, 0.0) + (1 - weights.target_share) * max(cluster, 0.0),
        placement_churn=churn, resource_fraction=RESOURCE_FRACTION[candidate.resource_cost],
        confidence=candidate.confidence,
        churn_factor=1 - weights.disruption_discount * churn,
        resource_factor=1 - weights.resource_discount * RESOURCE_FRACTION[candidate.resource_cost],
        blast_before=before.blast_radius, blast_after=after.blast_radius,
        mean_blast_before=mean_before, mean_blast_after=mean_after,
        worst_case_before=worst_before, worst_case_after=worst_after,
        survivability_before=before.survivability, survivability_after=after.survivability,
        basis=before.basis, failed_node=before.failed_node)

    if target < -tolerance:
        return _excluded(candidate, ExclusionReason.WORSENS_ASSESSED_NODE,
                         "the assessed node would be more at risk than now", components)
    if worst_case < -tolerance:
        return _excluded(candidate, ExclusionReason.WORSENS_WORST_CASE_NODE,
                         "some node would end up riskier than the riskiest node is now", components)
    if cluster <= tolerance and worst_case <= tolerance:
        if target > tolerance:
            return _excluded(candidate, ExclusionReason.RISK_TRANSFER_ONLY,
                             "the assessed node improves but neither the cluster average nor the worst case does: "
                             "the risk only moved to other nodes", components)
        return _excluded(candidate, ExclusionReason.NO_PREDICTED_IMPROVEMENT,
                         "Component 3 predicts no reduction in risk", components)
    if components.benefit <= tolerance:
        return _excluded(candidate, ExclusionReason.NO_PREDICTED_IMPROVEMENT,
                         "Component 3 predicts no reduction in risk at the assessed node", components)

    score = components.confidence * components.benefit * components.churn_factor * components.resource_factor
    return RankedCandidate(candidate.id, candidate.type, candidate.service, None, score, components, None,
                           "ranked by score")


def rank_candidates(
    evaluated: Sequence[EvaluatedCandidate],
    weights: RankingWeights | None = None,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> list[RankedCandidate]:
    """Every candidate, ranked ones first. Raises ValueError if they were not assessed against one failure.

    Order: score, then less placement churn, then cheaper, then the less restrictive strategy (``RESTRICTIVENESS``),
    then service, type and id, so the result never depends on the order the candidates arrive in.
    """
    weights = weights or RankingWeights()
    shared = {(c.before.failed_node, c.before.basis) for c in evaluated if isinstance(c.before, ImpactSnapshot)}
    if len({failed for failed, _ in shared}) > 1:
        raise ValueError("candidates must be assessed against the same failure (the same assessed node)")
    if len({basis for _, basis in shared}) > 1:
        raise ValueError("candidates must be ranked on the same basis (single workflow or one portfolio mode)")

    scored = [_score(candidate, weights, tolerance) for candidate in evaluated]
    ranked = sorted((r for r in scored if r.score is not None),
                    key=lambda r: (-r.score, r.components.placement_churn, r.components.resource_fraction,
                                   RESTRICTIVENESS.get(r.type, len(RESTRICTIVENESS)), r.service, r.type, r.id))
    excluded = sorted((r for r in scored if r.score is None), key=lambda r: (r.service, r.type, r.id))
    return [RankedCandidate(r.id, r.type, r.service, position, r.score, r.components, None, r.detail)
            for position, r in enumerate(ranked, start=1)] + excluded
