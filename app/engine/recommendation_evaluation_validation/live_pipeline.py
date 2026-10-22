"""Live recommendations: a RecommendationReport computed from the connected cluster, never from a fixture.

Where each input comes from, all through the other components' public interfaces:
* the approved, frozen workflow and the live topology (with how complete it is) come from Component 3's
  runtime provider, which refuses with a coded error when either is missing or the topology is too incomplete
  for the services the workflow uses;
* the blast radius before a change, and again after it, is Component 3's predictor. Nothing here recomputes it;
* the candidate changes and their feasibility and simulation are Component 4's own generator, feasibility
  check and what-if simulator, which work on a deep copy and never change the real graph.

A candidate is ``feasible`` only when the feasibility check verified it. A candidate whose scheduling
constraints cannot be verified from the live evidence (for example node taints, which the topology contract
does not carry) is reported ``feasible = False`` with its status INSUFFICIENT_EVIDENCE and the missing facts in
``evidence["feasibility"]``; it is never reported as confirmed feasible, so it cannot be approved.

A recommendation is a prediction about a virtual change, not an observation, and nothing here applies it:
approving it is a separate step that needs a developer.

Two numbers in the contract have no ground truth to measure, so they are defined here rather than guessed:
* ``confidence`` is evidence coverage: the share of the service's declared replicas whose placement was
  actually observed (0 to 1). It says how much of the picture was seen. It is not the accuracy of the model;
* ``score`` is the ranking score of ``ranking.py`` (see its docstring for the formula and the experimental
  weights). Only a verified FEASIBLE candidate with a predicted improvement gets one and a rank; every other
  candidate stays in the report with ``score = 0`` and its reason under ``evidence["ranking"]``. The selected
  recommendation is the top-ranked one. Selecting it is not approving it, and nothing here applies it.

The ranking is single-workflow: it compares the blast radius of the one frozen workflow before and after.

Single changes are scored on their own, so two changes that only work together would not be found. Pairs of
individually verified changes are therefore also evaluated (``combinations.py``): each pair is applied to a
virtual copy through the capacity ledger, re-checked in its final state, and sized by Component 3 like any other
candidate. A pair is one logical proposal; it is listed and ranked only if it was verified jointly and every
member matters. At most two members and a configurable number of pairs are looked at, and what was left out is
reported. The pair is verified for the final state only; rollout time is reported as not evaluated.
"""
from typing import Any, NamedTuple

from app.engine.failure_impact_prediction.runtime_provider import get_runtime_component_inputs
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.approval import LIVE_EVIDENCE_SOURCE, LiveRecommendation
from app.engine.recommendation_evaluation_validation.candidate_generation import (
    CandidateContextBuilder,
    generate_candidates,
)
from app.engine.recommendation_evaluation_validation.candidate_generation import CandidateSpec
from app.engine.recommendation_evaluation_validation.combinations import (
    COMBINATION,
    DEFAULT_EVALUATION_BUDGET,
    ROLLOUT_TIME,
    Member,
    combination_id,
    combine_pair,
    combined_simulation,
    plan_pairs,
    redundant_members,
)
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    UNSCHEDULED_NODE,
    FeasibilityStatus,
    SchedulingEvidence,
    scheduling_basis,
)
from app.engine.recommendation_evaluation_validation.ranking import (
    FORMULA_VERSION,
    SINGLE_WORKFLOW,
    EvaluatedCandidate,
    ImpactSnapshot,
    RankingWeights,
    rank_candidates,
)
from app.engine.recommendation_evaluation_validation.service import evaluate_candidate
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.recommendation import RecommendationCandidate, RecommendationReport

COMPONENT = "recommendation"

# A topology spread constraint adds no replicas, so it needs no extra capacity. The disruption of applying it
# (a pod-template edit rolls out every pod of the Deployment) is a separate, future operational cost.
RESOURCE_COST = {"TOPOLOGY_SPREAD": "LOW"}

# Ordered so the dearest member decides the cost of a combination.
COST_ORDER = ("LOW", "MEDIUM", "HIGH")

CONFIDENCE_MEANING = (
    "Evidence coverage: the share of the service's declared replicas whose placement was observed. "
    "It is not the accuracy of the prediction."
)


PLACEMENT_CHURN_MEANING = (
    "Placement churn: the share of the service's ready replicas whose target node differs from the node they "
    "run on now. It is not the number of pods a rollout restarts: applying the constraint edits the "
    "Deployment's pod template, which eventually replaces every pod of that Deployment. That rollout cost is "
    "not modelled."
)


class _Outcome(NamedTuple):
    """What the report needs about one candidate besides its ranking entry."""

    type: str
    service: str
    feasible: bool
    predicted: float
    reduction: float
    confidence: float
    cost: str


def _highest_cost(costs: list[str]) -> str:
    """The dearest cost label of a combination's members; an unknown label makes the whole cost unknown."""
    if any(cost not in COST_ORDER for cost in costs):
        return "UNKNOWN"
    return max(costs, key=COST_ORDER.index)


def _impact_snapshot(report) -> ImpactSnapshot:
    """Component 3's report for one assumed node failure, reduced to what ranking uses."""
    return ImpactSnapshot(
        failed_node=report.failed_node,
        blast_radius=report.blast_radius,
        survivability=report.survivability,
        node_blast={risk.node: risk.blast_radius for risk in report.node_risk_ranking},
        basis=SINGLE_WORKFLOW,
    )


def scheduling_evidence_for(deployment) -> SchedulingEvidence:
    """The scheduling facts the live topology cannot carry (node taints), as far as anything supplies them.

    Nothing reads taints from the cluster yet, so today this is empty and taints are reported unknown.
    """
    return SchedulingEvidence.from_graph(deployment)


def _invalid(message: str) -> EvidenceError:
    return EvidenceError("INVALID_RECOMMENDATION_REQUEST", message, http_status=422, component=COMPONENT)


def _riskiest_node(workflow, deployment) -> str:
    """The worker whose loss has the largest blast radius (ties: lowest name), by Component 3's own ranking."""
    if not deployment.nodes:
        raise EvidenceError(
            "RECOMMENDATION_EVIDENCE_UNAVAILABLE",
            "The topology has no worker nodes, so there is no failure to recommend against.",
            http_status=409, component=COMPONENT,
        )
    ranking = predict_failure_impact(workflow, deployment, deployment.nodes[0].name).node_risk_ranking
    if not ranking:
        return deployment.nodes[0].name
    return max(ranking, key=lambda risk: (risk.blast_radius, tuple(-ord(ch) for ch in risk.node))).node


def _coverage(deployment, service_name: str) -> float:
    """Share of the service's declared replicas that have an observed placement (0 to 1)."""
    service = next((s for s in deployment.services if s.name == service_name), None)
    if service is None or not service.declared_replicas:
        return 0.0
    placed = sum(1 for pod in service.pods if pod.node != UNSCHEDULED_NODE)
    return round(min(1.0, placed / service.declared_replicas), 4)


class _Combinations(NamedTuple):
    candidates: list[EvaluatedCandidate]
    outcomes: dict[str, _Outcome]
    feasibility: dict[str, Any]
    evidence: dict[str, Any]
    details: dict[str, Any]


def _dump(graph) -> dict[str, Any] | None:
    """A DeploymentGraph as its contract JSON, or None when there is none."""
    return graph.model_dump(by_alias=True) if graph is not None else None


def _current_placement(deployment, service_name: str) -> list[dict[str, Any]]:
    """Where the service's pods run now, exactly as the live topology reports it."""
    service = next((s for s in deployment.services if s.name == service_name), None)
    if service is None:
        return []
    return [{"pod": pod.name, "node": pod.node, "ready": pod.ready} for pod in service.pods]


def _evaluate_combinations(
    workflow, deployment, failed_node: str, before, before_snapshot: ImpactSnapshot, members: list[Member],
    singles_after: dict[str, ImpactSnapshot | None], singles: dict[str, _Outcome], ready_replicas: dict[str, int],
    scheduling_evidence: SchedulingEvidence, budget: int,
) -> _Combinations:
    """Pairs of individually verified changes, looked at in a fixed order up to the budget.

    A pair is returned for ranking only when it was verified jointly and neither member is redundant. Every pair
    that was looked at, kept or not, is described in the evidence with the reason.
    """
    plan = plan_pairs(members, budget)
    candidates: list[EvaluatedCandidate] = []
    outcomes: dict[str, _Outcome] = {}
    feasibility: dict[str, Any] = {}
    details: dict[str, Any] = {}
    entries: dict[str, Any] = {}
    for first, second in plan.pairs:
        result = combine_pair(deployment, first, second, scheduling_evidence)
        combo_id = combination_id(workflow.workflow_id, result.services)
        feasibility[combo_id] = result.feasibility.to_dict() | {
            "constraintsDigest": scheduling_basis(deployment, result.services)}
        entry: dict[str, Any] = {
            "members": list(result.members), "services": list(result.services), "status": result.status.value,
            "reason": result.reason, "order": list(result.order) if result.order else None,
            "attempts": list(result.attempts), "excluded": None,
        }
        entries[combo_id] = entry
        if result.status is not FeasibilityStatus.FEASIBLE:
            entry["excluded"] = "NOT_VERIFIED_JOINTLY"
            continue

        simulation = combined_simulation(deployment, result)
        try:
            after = predict_failure_impact(workflow, result.deployment, failed_node)
        except ValueError:      # Component 3 could not evaluate the final virtual state
            after = None
        after_snapshot = _impact_snapshot(after) if after is not None else None
        member_impacts = {m.service: singles_after.get(m.id) for m in (first, second)}
        redundant = redundant_members(before_snapshot, after_snapshot, member_impacts)
        entry["memberBlastAfter"] = {s: (i.blast_radius if i else None) for s, i in member_impacts.items()}
        entry["combinedBlastAfter"] = after_snapshot.blast_radius if after_snapshot else None
        if redundant is None:
            entry["excluded"] = "MEMBER_IMPACT_UNKNOWN"
            continue
        if redundant:
            entry["excluded"] = "REDUNDANT_MEMBER"
            entry["redundantMembers"] = redundant
            continue

        costs = [singles[m.id].cost for m in (first, second)]
        coverage = min(singles[m.id].confidence for m in (first, second))
        details[combo_id] = {
            "rootCause": "Two individually verified changes that reduce the blast radius only together.",
            "services": list(result.services), "members": list(result.members),
            "currentPlacement": {svc: _current_placement(deployment, svc) for svc in result.services},
            "simulatedDeployment": _dump(simulation.simulated_deployment),
            "podsToRecreate": list(simulation.pods_to_recreate),
        }
        outcomes[combo_id] = _Outcome(
            COMBINATION, simulation.service, True, after.blast_radius,
            round(before.blast_radius - after.blast_radius, 2), coverage, _highest_cost(costs))
        candidates.append(EvaluatedCandidate(
            id=combo_id, type=COMBINATION, service=simulation.service, simulation=simulation,
            before=before_snapshot, after=after_snapshot,
            ready_replicas=sum(ready_replicas[s] for s in result.services),
            confidence=coverage, resource_cost=_highest_cost(costs)))

    return _Combinations(candidates, outcomes, feasibility, {
        "maxMembers": 2,
        "evaluationBudget": budget,
        "considered": plan.considered,
        "evaluated": len(plan.pairs),
        "truncated": plan.truncated,
        "skippedPairs": [list(pair) for pair in plan.skipped],
        "rolloutTime": ROLLOUT_TIME,
        "scope": "The final state of the two changes together, as one logical proposal; nothing is applied.",
        "entries": entries,
    }, details)


class EvidenceProvenance(NamedTuple):
    """Where the inputs of one evaluation came from. ``source`` is what the approval gate trusts or refuses."""

    source: str
    namespace: str
    topology_captured_at: str | None
    topology_status: str | None
    workflow_freeze_id: str | None


def build_live_recommendation(
    workflow_id: str,
    failed_node: str | None = None,
    namespace: str = "default",
    *,
    combination_budget: int = DEFAULT_EVALUATION_BUDGET,
) -> LiveRecommendation:
    """The recommendation report for one frozen workflow on the connected cluster, with its evidence.

    ``failed_node`` is the worker to protect against. When it is not given, the worker whose loss would hurt
    most is used, and the evidence says so. Every failure to read the cluster or the workflow is the coded
    error the other components already raise; none of them is replaced by a fixture. ``combination_budget`` is
    the most pairs of changes evaluated together; what the budget left out is reported in the evidence.
    """
    inputs = get_runtime_component_inputs(workflow_id, namespace)
    return evaluate_recommendation(
        inputs.workflow, inputs.deployment,
        EvidenceProvenance(LIVE_EVIDENCE_SOURCE, inputs.namespace, inputs.topology_captured_at,
                           inputs.topology_status, inputs.workflow_freeze_id),
        failed_node, scheduling_evidence=scheduling_evidence_for(inputs.deployment),
        combination_budget=combination_budget,
    )


def evaluate_recommendation(
    workflow,
    deployment,
    provenance: EvidenceProvenance,
    failed_node: str | None = None,
    *,
    scheduling_evidence: SchedulingEvidence,
    combination_budget: int = DEFAULT_EVALUATION_BUDGET,
) -> LiveRecommendation:
    """The pipeline over a workflow and a topology that are already in hand.

    This is the whole evaluation (candidates, feasibility, virtual simulation, Component 3's before/after,
    ranking, pairs) with the fetching taken out, so the same code serves the live route and an offline
    evaluation of captured or controlled artifacts. It reads no cluster and changes neither input. What the
    result may be used for is decided by ``provenance.source`` alone: only the live route passes
    ``LIVE_EVIDENCE_SOURCE``, and an approval is refused for any other source.
    """
    workflow_id = workflow.workflow_id
    chosen_by = "requested"
    if not failed_node:
        failed_node, chosen_by = _riskiest_node(workflow, deployment), "highest blast radius"
    try:
        before = predict_failure_impact(workflow, deployment, failed_node)
    except ValueError as error:  # an unknown failed node
        raise _invalid(str(error)) from error

    skipped: list[str] = []
    feasibility: dict[str, Any] = {}
    before_snapshot = _impact_snapshot(before)
    evaluated: list[EvaluatedCandidate] = []
    outcomes: dict[str, _Outcome] = {}
    members: list[Member] = []
    candidate_details: dict[str, Any] = {}
    singles_after: dict[str, ImpactSnapshot | None] = {}
    ready_replicas: dict[str, int] = {}
    for spec in generate_candidates(CandidateContextBuilder.build(deployment, before)):
        try:
            simulated = evaluate_candidate(deployment, spec, scheduling_evidence)
        except ValueError:
            skipped.append(spec.recommendation_type)  # a type the simulator cannot evaluate yet
            continue

        coverage = _coverage(deployment, spec.service)
        after_snapshot, predicted, reduction = None, before.blast_radius, 0.0
        if simulated.feasible:
            try:
                after = predict_failure_impact(workflow, simulated.simulated_deployment, failed_node)
            except ValueError:  # Component 3 could not evaluate the simulated deployment: no impact evidence
                after = None
            if after is not None:
                after_snapshot = _impact_snapshot(after)
                predicted, reduction = after.blast_radius, round(before.blast_radius - after.blast_radius, 2)
        candidate_id = f"rec-{workflow_id}-{spec.service}-{spec.recommendation_type}".lower().replace("_", "-")
        feasibility[candidate_id] = simulated.feasibility.to_dict() | {
            "constraintsDigest": scheduling_basis(deployment, [spec.service])}
        cost = RESOURCE_COST.get(spec.recommendation_type, "UNKNOWN")
        outcomes[candidate_id] = _Outcome(spec.recommendation_type, spec.service, simulated.feasible, predicted,
                                          reduction, coverage, cost)
        service = next((svc for svc in deployment.services if svc.name == spec.service), None)
        ready_replicas[spec.service] = sum(1 for pod in service.pods if pod.ready) if service else 0
        singles_after[candidate_id] = after_snapshot
        candidate_details[candidate_id] = {
            "rootCause": spec.reason,
            "service": spec.service,
            "feasibilityReason": simulated.feasibility_reason,
            "declaredReplicas": service.declared_replicas if service else None,
            "readyReplicas": ready_replicas[spec.service],
            "currentPlacement": {spec.service: _current_placement(deployment, spec.service)},
            "simulatedDeployment": _dump(simulated.simulated_deployment) if simulated.feasible else None,
            "podsToRecreate": list(simulated.pods_to_recreate),
        }
        if simulated.feasible:      # verified individually, even when it improves nothing on its own
            members.append(Member(id=candidate_id, service=spec.service, feasibility=simulated.feasibility))
        evaluated.append(EvaluatedCandidate(
            id=candidate_id, type=spec.recommendation_type, service=spec.service, simulation=simulated,
            before=before_snapshot, after=after_snapshot, ready_replicas=ready_replicas[spec.service],
            confidence=coverage, resource_cost=cost,
        ))

    combos = _evaluate_combinations(
        workflow, deployment, failed_node, before, before_snapshot, members, singles_after, outcomes, ready_replicas,
        scheduling_evidence, combination_budget)
    evaluated += combos.candidates
    outcomes.update(combos.outcomes)
    feasibility.update(combos.feasibility)
    candidate_details.update(combos.details)

    weights = RankingWeights()
    ranked = rank_candidates(evaluated, weights)
    candidates: list[RecommendationCandidate] = []
    for entry in ranked:
        outcome = outcomes[entry.id]
        candidates.append(RecommendationCandidate(
            id=entry.id,
            type=outcome.type,
            service=outcome.service,
            feasible=outcome.feasible,
            predicted_blast_radius=outcome.predicted,
            risk_reduction=outcome.reduction,
            resource_cost=outcome.cost,
            confidence=outcome.confidence,
            score=round(entry.score, 4) if entry.score is not None else 0.0,
        ))

    selected = next((entry.id for entry in ranked if entry.rank == 1), None)
    report = RecommendationReport(
        workflow_id=workflow.workflow_id,
        recommendations=candidates,
        selected_recommendation_id=selected,
    )
    evidence: dict[str, Any] = {
        "source": provenance.source,
        "topologyCapturedAt": provenance.topology_captured_at,
        "topologyStatus": provenance.topology_status,
        "namespace": provenance.namespace,
        "workflowId": workflow.workflow_id,
        "workflowFreezeId": provenance.workflow_freeze_id,
        "topologySnapshotId": deployment.snapshot_id,
        "failedNode": failed_node,
        "failedNodeChosen": chosen_by,
        "blastRadiusBefore": before.blast_radius,
        "confidenceMeaning": CONFIDENCE_MEANING,
        "skippedUnsupportedTypes": sorted(set(skipped)),
        "feasibility": feasibility,
        "ranking": {
            "formulaVersion": FORMULA_VERSION,
            "weights": weights.to_dict(),
            "basis": SINGLE_WORKFLOW,
            "placementChurnMeaning": PLACEMENT_CHURN_MEANING,
            "selectedCandidate": selected,
            "candidates": {entry.id: entry.to_dict() for entry in ranked},
            "combinedCandidates": "pairs of verified changes are evaluated separately, see evidence['combinations']",
        },
        "combinations": combos.evidence,
        "isPrediction": True,
    }
    return LiveRecommendation(
        report=report, evidence=evidence,
        details={"currentDeployment": _dump(deployment), "candidates": candidate_details},
    )
