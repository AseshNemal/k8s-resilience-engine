"""Bounded combinations of two compatible candidate changes (Member 4, Phase 2B).

Why. A change can look worthless on its own and still be needed: spreading ``db`` rescues nothing while ``cart``
is still stuck on the failing node, and the other way round. Scored one at a time, neither is recommended. This
module looks at pairs, and only pairs.

What a pair is. Two candidates for two different services, each individually verified FEASIBLE (a candidate with
no standalone improvement still qualifies; one that is not verified feasible never does). The pair is ONE logical
proposal: one id, one placement, one approval. Nothing is applied to anything.

How a pair is verified. The first change is applied to a virtual copy through the capacity ledger, so the nodes
carry the room it left. The second is planned on that copy (so every scheduling constraint and the shared node
capacity are checked again), applied, and then BOTH services are checked in the final state with their
placements held fixed, which catches a second change that breaks a constraint of the first. Both orders are
tried and the one that moves fewer pods is kept. Only a placement that passes all of this is FEASIBLE.

What a failure means. If no order works, the pair is INSUFFICIENT_EVIDENCE, never INFEASIBLE: applying two
individually planned changes one after the other does not search every joint arrangement, so failing is not a
proof that none exists. Unknown evidence stays unknown and fails closed the same way.

Final state, not rollout time. The verdict is for the state after both changes are in place. Whether the two
Deployments can be rolled out in that state (surge pods, update order, the transient) depends on their update
strategy, which is not in the evidence; it is reported as not evaluated and is not modelled.

Bounds. At most two members, and at most ``budget`` pairs evaluated per request, in a fixed order; what was
skipped is reported. Impact is not computed here: the caller asks Component 3 about the final virtual state.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from app.engine.recommendation_evaluation_validation.capacity_ledger import (
    CapacityExceeded,
    CapacityLedger,
    LedgerError,
    LedgerPlacementMutation,
    UnknownCapacity,
)
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    ASSUMPTIONS,
    DEFAULT_MAX_SKEW,
    DEFAULT_SEARCH_BUDGET,
    NOT_MODELED,
    ConstraintFinding,
    FeasibilityResult,
    FeasibilityStatus,
    SchedulingEvidence,
    check_topology_spread_feasibility,
)
from app.engine.recommendation_evaluation_validation.metrics import calculate_replica_concentration
from app.engine.recommendation_evaluation_validation.ranking import DEFAULT_TOLERANCE, ImpactSnapshot, impact_reductions
from app.engine.recommendation_evaluation_validation.simulation_result import SimulationResult
from app.engine.simulation import simulate_deployment_change
from app.shared.schemas.deployment import DeploymentGraph

COMBINATION = "COMBINATION"
DEFAULT_EVALUATION_BUDGET = 20

FINAL_STATE_ASSUMPTION = (
    "The combination is verified for the final state only, with both changes in place. Rollout-time feasibility "
    "(surge pods, update order across the two Deployments) is not evaluated: it depends on the update strategy, "
    "which is not in the evidence."
)
ROLLOUT_NOT_MODELED = "rollout-time feasibility of applying two Deployment changes (surge, ordering, transient)"
ROLLOUT_TIME = {
    "status": "NOT_EVALUATED",
    "reason": "Needs the Deployments' update strategy (maxSurge, maxUnavailable), which is not in the evidence.",
}


@dataclass(frozen=True)
class Member:
    """A candidate that is individually verified FEASIBLE, with the placement that verification found."""

    id: str
    service: str
    feasibility: FeasibilityResult | None


@dataclass(frozen=True)
class PairPlan:
    pairs: tuple[tuple[Member, Member], ...]
    considered: int                              # all compatible pairs
    skipped: tuple[tuple[str, str], ...] = ()    # (service, service) of the pairs the budget left out

    @property
    def truncated(self) -> bool:
        return bool(self.skipped)


def plan_pairs(members: Sequence[Member], budget: int) -> PairPlan:
    """The compatible pairs in a fixed order (by service, then id), at most ``budget`` of them."""
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
        raise ValueError("the combination evaluation budget must be a whole number, zero or more")
    ordered = sorted(members, key=lambda m: (m.service, m.id))
    compatible = [(a, b) for a, b in combinations(ordered, 2) if a.service != b.service]
    return PairPlan(
        pairs=tuple(compatible[:budget]),
        considered=len(compatible),
        skipped=tuple((a.service, b.service) for a, b in compatible[budget:]),
    )


def combination_id(workflow_id: str, services: Sequence[str]) -> str:
    return f"rec-{workflow_id}-{'+'.join(sorted(services))}-combination".lower().replace("_", "-")


@dataclass(frozen=True)
class PairOutcome:
    members: tuple[str, str]                      # candidate ids, in service order
    services: tuple[str, str]
    status: FeasibilityStatus                     # FEASIBLE, or INSUFFICIENT_EVIDENCE (never INFEASIBLE)
    reason: str
    order: tuple[str, str] | None                 # services in the order they were applied
    deployment: DeploymentGraph | None            # the final virtual state; None unless FEASIBLE
    feasibility: FeasibilityResult
    attempts: tuple[str, ...] = ()                # why each order that failed failed


@dataclass(frozen=True)
class _Applied:
    order: tuple[str, str]
    deployment: DeploymentGraph
    placement: dict[str, str]
    recreate: tuple[str, ...]
    findings: tuple[ConstraintFinding, ...]


@dataclass(frozen=True)
class _Failed:
    message: str
    missing: tuple[str, ...] = ()


def _apply(deployment: DeploymentGraph, member_service: str, placement: Mapping[str, str]) -> DeploymentGraph:
    return simulate_deployment_change(deployment, LedgerPlacementMutation(member_service, placement)).after


def _refused(label: str, error: LedgerError) -> _Failed:
    if isinstance(error, CapacityExceeded):
        return _Failed(f"{label}: not enough capacity ({error})")
    if isinstance(error, UnknownCapacity):
        return _Failed(f"{label}: unknown capacity ({error})", (f"capacity of node {error.node}: {error.reason}",))
    return _Failed(f"{label}: {error}")


def _try_order(deployment: DeploymentGraph, lead: Member, follow: Member, evidence: SchedulingEvidence,
               max_skew: int, search_budget: int) -> _Applied | _Failed:
    label = f"{lead.service} then {follow.service}"
    try:
        after_lead = _apply(deployment, lead.service, lead.feasibility.placement)
    except LedgerError as error:
        return _refused(label, error)

    plan = check_topology_spread_feasibility(after_lead, follow.service, evidence, max_skew=max_skew,
                                             search_budget=search_budget)
    if plan.status is not FeasibilityStatus.FEASIBLE:
        blocked_by = ", ".join(sorted({f.constraint for f in plan.findings})) or "no verified placement"
        return _Failed(f"{label}: {follow.service} cannot be placed once {lead.service} has moved "
                       f"({plan.status.value}; blocked by {blocked_by})", plan.missing_evidence)
    try:
        final = _apply(after_lead, follow.service, plan.placement)
    except LedgerError as error:
        return _refused(label, error)

    for member in (lead, follow):       # the first change must still hold where the second one put its pods
        held = check_topology_spread_feasibility(final, member.service, evidence, max_skew=max_skew,
                                                 search_budget=search_budget, hold_placement=True)
        if held.status is not FeasibilityStatus.FEASIBLE:
            return _Failed(f"{label}: in the final state {member.service} no longer satisfies its constraints "
                           f"({held.status.value}: {held.reason})", held.missing_evidence)

    return _Applied(
        order=(lead.service, follow.service), deployment=final,
        placement={**lead.feasibility.placement, **plan.placement},
        recreate=tuple(sorted(set(lead.feasibility.pods_to_recreate) | set(plan.pods_to_recreate))),
        findings=lead.feasibility.findings + plan.findings)


def combine_pair(
    deployment: DeploymentGraph,
    first: Member,
    second: Member,
    evidence: SchedulingEvidence,
    *,
    max_skew: int = DEFAULT_MAX_SKEW,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
) -> PairOutcome:
    """Verify two individually feasible changes together. The deployment passed in is never changed."""
    ordered = sorted((first, second), key=lambda m: (m.service, m.id))
    services = (ordered[0].service, ordered[1].service)
    ids = (ordered[0].id, ordered[1].id)

    attempts: list[_Applied | _Failed] = [
        _try_order(deployment, lead, follow, evidence, max_skew, search_budget)
        for lead, follow in (tuple(ordered), tuple(reversed(ordered)))]
    worked = [a for a in attempts if isinstance(a, _Applied)]

    if worked:
        best = min(worked, key=lambda a: (len(a.recreate), 0 if a.order == services else 1))
        feasibility = FeasibilityResult(
            status=FeasibilityStatus.FEASIBLE,
            reason=(f"Applied together ({' then '.join(best.order)}), {services[0]} and {services[1]} can both be "
                    f"placed so that neither is left on a single node, with every modelled constraint and the "
                    f"shared node capacity re-checked in the final state. {len(best.recreate)} replica(s) have a "
                    f"different target node than today (placement churn). This is a model prediction, not a "
                    f"scheduler guarantee, and rollout-time feasibility was not evaluated."),
            scheduling=FeasibilityStatus.FEASIBLE, single_node_resilience=True, findings=best.findings,
            placement=best.placement, pods_to_recreate=best.recreate,
            assumptions=ASSUMPTIONS + (FINAL_STATE_ASSUMPTION,), not_modeled=NOT_MODELED + (ROLLOUT_NOT_MODELED,))
        return PairOutcome(ids, services, FeasibilityStatus.FEASIBLE, feasibility.reason, best.order,
                           best.deployment, feasibility)

    failures = [a for a in attempts if isinstance(a, _Failed)]
    why = tuple(f.message for f in failures)
    missing = tuple(sorted({item for f in failures for item in f.missing}))
    reason = (f"No joint placement of {services[0]} and {services[1]} was found by applying one change after the "
              f"other ({'; '.join(why)}). A joint search over every arrangement was not performed, so this is "
              f"not a proof that the pair is infeasible, and it is not reported feasible.")
    feasibility = FeasibilityResult(
        status=FeasibilityStatus.INSUFFICIENT_EVIDENCE, reason=reason, scheduling=FeasibilityStatus.INSUFFICIENT_EVIDENCE,
        findings=(ConstraintFinding("JOINT_PLACEMENT_NOT_FOUND", FeasibilityStatus.INSUFFICIENT_EVIDENCE,
                                    "no sequential joint placement verified; joint search not performed"),),
        missing_evidence=missing, assumptions=ASSUMPTIONS + (FINAL_STATE_ASSUMPTION,),
        not_modeled=NOT_MODELED + (ROLLOUT_NOT_MODELED,))
    return PairOutcome(ids, services, FeasibilityStatus.INSUFFICIENT_EVIDENCE, reason, None, None, feasibility, why)


def combined_simulation(base: DeploymentGraph, outcome: PairOutcome) -> SimulationResult:
    """The pair as one SimulationResult, the shape the ranking and the approval gate already understand."""
    verified = outcome.status is FeasibilityStatus.FEASIBLE
    before = sum(calculate_replica_concentration(base, s) for s in outcome.services) / 2
    after = (sum(calculate_replica_concentration(outcome.deployment, s) for s in outcome.services) / 2
             if verified else before)
    return SimulationResult(
        recommendation_type=COMBINATION, service="+".join(outcome.services), feasible=verified,
        feasibility_reason=outcome.reason, concentration_before=before, concentration_after=after,
        concentration_reduction=round(before - after, 2),
        simulated_deployment=outcome.deployment if verified else None,
        feasibility_status=outcome.status, feasibility=outcome.feasibility,
        pods_to_recreate=outcome.feasibility.pods_to_recreate if verified else ())


def redundant_members(
    before: ImpactSnapshot | None,
    combined: ImpactSnapshot | None,
    singles: Mapping[str, ImpactSnapshot | None],
    tolerance: float = DEFAULT_TOLERANCE,
) -> list[str] | None:
    """The members that add nothing: the other member alone already does everything the pair does.

    A pair is worth proposing only if each member matters. A member with no standalone improvement is not
    redundant by that fact, because the pair can still improve on every single. None means this cannot be judged
    (a missing or unusable impact), and a pair that cannot be shown to need both members is not proposed.
    """
    pair = impact_reductions(before, combined, tolerance)
    reductions = {service: impact_reductions(before, snapshot, tolerance) for service, snapshot in singles.items()}
    if pair is None or any(r is None for r in reductions.values()):
        return None
    return sorted(
        service for service in reductions
        if all(pair[0] <= other[0] + tolerance and pair[1] <= other[1] + tolerance
               for name, other in reductions.items() if name != service))
