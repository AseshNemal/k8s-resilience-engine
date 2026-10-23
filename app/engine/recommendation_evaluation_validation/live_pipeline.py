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
import dataclasses
from typing import Any, NamedTuple

from app.engine.failure_impact_prediction.runtime_provider import get_runtime_component_inputs
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.approval import (
    DECLARED_WORKFLOW_SOURCE,
    LIVE_EVIDENCE_SOURCE,
    LiveRecommendation,
)
from app.engine.recommendation_evaluation_validation.candidate_generation import (
    CandidateContextBuilder,
    generate_candidates,
)
from app.engine.recommendation_evaluation_validation.candidate_generation import CandidateSpec
from app.engine.recommendation_evaluation_validation.combinations import (
    COMBINATION,
    DEFAULT_EVALUATION_BUDGET,
    PAIRABLE,
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
from app.engine.recommendation_evaluation_validation.declared_workflow import get_declared_workflow
from app.engine.recommendation_evaluation_validation.live_scheduling_evidence import (
    enrich_scheduling_facts,
    live_scheduling_evidence,
)
from app.engine.recommendation_evaluation_validation.service import evaluate_candidate
from app.engine.recommendation_evaluation_validation.strategy_feasibility import (
    ANTI_AFFINITY_MODEL,
    REPLICA_INCREASE_ANTI_AFFINITY_MODEL,
    REPLICA_INCREASE_MODEL,
    REPLICA_INCREASE_PLUS_2_MODEL,
)
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.recommendation import RecommendationCandidate, RecommendationReport

COMPONENT = "recommendation"

# Spread and anti-affinity add no replicas, so they need no extra capacity; a replica increase adds one. The disruption of applying it
# (a pod-template edit rolls out every pod of the Deployment) is a separate, future operational cost.
RESOURCE_COST = {"TOPOLOGY_SPREAD": "LOW", "POD_ANTI_AFFINITY": "LOW", "REPLICA_INCREASE": "MEDIUM",
                 "REPLICA_INCREASE_ANTI_AFFINITY": "MEDIUM", "REPLICA_INCREASE_PLUS_2": "HIGH"}

# The model each strategy's verdict was reached under (None: the plain spread's, which is the default of the digest).
STRATEGY_MODELS = {"POD_ANTI_AFFINITY": ANTI_AFFINITY_MODEL, "REPLICA_INCREASE": REPLICA_INCREASE_MODEL,
                   "REPLICA_INCREASE_ANTI_AFFINITY": REPLICA_INCREASE_ANTI_AFFINITY_MODEL,
                   "REPLICA_INCREASE_PLUS_2": REPLICA_INCREASE_PLUS_2_MODEL}

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


def impact_detail(report) -> dict[str, Any]:
    """Component 3's report for one assumed node failure, as the display detail the application analysis keeps.

    Kept in ``details`` (display only, never part of an approval's fingerprint), so the pre-change baseline and the
    proposal's prediction can be shown beside the post-change recalculation without being computed again.
    """
    return {
        "blastRadius": report.blast_radius, "survivability": report.survivability,
        "failedServices": list(report.failed_services), "failedWorkflows": list(report.failed_workflows),
    }


def scheduling_evidence_for(deployment) -> SchedulingEvidence:
    """The scheduling facts the live topology cannot carry (node taints).

    No other component supplies them, so Member 4 reads them itself (``live_scheduling_evidence``), read-only. A node that
    cannot be read keeps its taints unknown; they are never defaulted to none.
    """
    return live_scheduling_evidence(deployment)


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
            "predictedImpact": impact_detail(after),
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
    deployment, facts = enrich_scheduling_facts(inputs.deployment, inputs.namespace)
    live = evaluate_recommendation(
        inputs.workflow, deployment,
        EvidenceProvenance(LIVE_EVIDENCE_SOURCE, inputs.namespace, inputs.topology_captured_at,
                           inputs.topology_status, inputs.workflow_freeze_id),
        failed_node, scheduling_evidence=scheduling_evidence_for(deployment),
        combination_budget=combination_budget,
    )
    return dataclasses.replace(live, evidence={**live.evidence, "schedulingFacts": facts})


def _require_complete_topology(workflow, discovery) -> None:
    """Refuse when a service the workflow uses has no complete observed placement (the same rule Component 3 applies to a frozen one)."""
    referenced = set(workflow.mandatory_services) | set(workflow.optional_services)
    for edge in workflow.edges:
        referenced.update((edge.source, edge.target))
    placed = {service.name for service in discovery.graph.services}
    blocked = {}
    for name in sorted(referenced):
        evidence = discovery.service_evidence.get(name)
        if name not in placed:
            blocked[name] = list(evidence.reasons) if evidence and evidence.reasons else ["SERVICE_NOT_DISCOVERED"]
        elif evidence is not None and not evidence.complete:
            blocked[name] = list(evidence.reasons)
    if blocked:
        raise EvidenceError(
            "TOPOLOGY_INCOMPLETE",
            "The topology is not complete enough for the services this declared workflow names, so nothing was evaluated.",
            http_status=409, component=COMPONENT,
            details={"services": [{"service": n, "reasons": r} for n, r in sorted(blocked.items())]})


require_complete_topology = _require_complete_topology     # public name for Member 4's own post-change analysis


def build_declared_recommendation(
    workflow_id: str,
    failed_node: str | None = None,
    namespace: str = "default",
    *,
    combination_budget: int = DEFAULT_EVALUATION_BUDGET,
    declaration_path: str | None = None,
) -> LiveRecommendation:
    """The recommendation for a workflow a person DECLARED, on the connected cluster's live topology. Never approvable.

    For when Component 1 has no frozen workflow to give. The workflow comes from Member 4's own declaration file
    (``declared_workflow``), the topology from Component 2 (``discover_live``, the same call Component 3's provider makes), the
    scheduling facts from Component 2's fuller analysis plus Member 4's taint read, and the failure sizing from Component 3's
    ``predict_failure_impact``. Nothing in another member's code is changed. The evidence is labelled ``DECLARED_WORKFLOW_SOURCE``,
    which the approval gate refuses, and it carries the declaration's identity and its limits.
    """
    from app.engine.deployment_topology_modeling.service import discover_live

    declared = get_declared_workflow(workflow_id, declaration_path)
    namespace = (namespace or "").strip()
    if not namespace:
        raise ValueError("namespace must be a non-empty string")
    discovery = discover_live(namespace=namespace)
    _require_complete_topology(declared.graph, discovery)
    deployment, facts = enrich_scheduling_facts(discovery.graph.model_copy(deep=True), namespace)
    live = evaluate_recommendation(
        declared.graph.model_copy(deep=True), deployment,
        EvidenceProvenance(DECLARED_WORKFLOW_SOURCE, namespace, discovery.captured_at, discovery.status, None),
        failed_node, scheduling_evidence=scheduling_evidence_for(deployment), combination_budget=combination_budget)
    return dataclasses.replace(live, evidence={**live.evidence, "schedulingFacts": facts, "declaredWorkflow": declared.to_dict()})


FAILURES_SCOPE = (
    "Each worker node is assessed as its own failure, with its own candidates and its own ranking. A ranking compares candidates against ONE assessed "
    "failure, so rankings of different failures are never merged or compared; open a failure to see its ranked candidates."
)


def failure_overview(workflow, deployment, provenance: EvidenceProvenance, *, scheduling_evidence: SchedulingEvidence,
                     combination_budget: int = DEFAULT_EVALUATION_BUDGET) -> dict[str, Any]:
    """Every worker node assessed as the failure, with that failure's own selected candidate. Reads nothing; changes nothing.

    A node is a single point of failure for the workflow when its loss has a blast radius above zero. Nodes are listed worst first
    (ties: by name). Per failure the full pipeline runs, so every number is the one the detail page shows for that node.
    """
    if not deployment.nodes:
        raise EvidenceError("RECOMMENDATION_EVIDENCE_UNAVAILABLE", "The topology has no worker nodes, so there is no failure to recommend against.",
                            http_status=409, component=COMPONENT)
    failures = []
    for node in sorted(n.name for n in deployment.nodes):
        live = evaluate_recommendation(workflow, deployment, provenance, node, scheduling_evidence=scheduling_evidence,
                                       combination_budget=combination_budget)
        ranked = live.evidence["ranking"]["candidates"]
        selected = next((c for c in live.report.recommendations if c.id == live.report.selected_recommendation_id), None)
        failures.append({
            "failedNode": node,
            "blastRadiusBefore": live.evidence["blastRadiusBefore"],
            "singlePointOfFailure": live.evidence["blastRadiusBefore"] > 0,
            "candidates": len(live.report.recommendations),
            "feasible": sum(1 for c in live.report.recommendations if c.feasible),
            "ranked": sum(1 for entry in ranked.values() if entry.get("rank") is not None),
            "selected": None if selected is None else {
                "id": selected.id, "type": selected.type, "service": selected.service, "score": selected.score,
                "predictedBlastRadius": selected.predicted_blast_radius, "riskReduction": selected.risk_reduction,
                "resourceCost": selected.resource_cost},
        })
    failures.sort(key=lambda row: (-row["blastRadiusBefore"], row["failedNode"]))
    return {"workflowId": workflow.workflow_id, "namespace": provenance.namespace, "source": provenance.source,
            "scope": FAILURES_SCOPE, "failures": failures,
            "singlePointsOfFailure": [row["failedNode"] for row in failures if row["singlePointOfFailure"]]}


def build_live_failures(workflow_id: str, namespace: str = "default", *, combination_budget: int = DEFAULT_EVALUATION_BUDGET) -> dict[str, Any]:
    """``failure_overview`` for a frozen workflow on the connected cluster (the same inputs as ``build_live_recommendation``)."""
    inputs = get_runtime_component_inputs(workflow_id, namespace)
    deployment, _facts = enrich_scheduling_facts(inputs.deployment, inputs.namespace)
    return failure_overview(
        inputs.workflow, deployment,
        EvidenceProvenance(LIVE_EVIDENCE_SOURCE, inputs.namespace, inputs.topology_captured_at, inputs.topology_status,
                           inputs.workflow_freeze_id),
        scheduling_evidence=scheduling_evidence_for(deployment), combination_budget=combination_budget)


def build_declared_failures(workflow_id: str, namespace: str = "default", *, combination_budget: int = DEFAULT_EVALUATION_BUDGET,
                            declaration_path: str | None = None) -> dict[str, Any]:
    """``failure_overview`` for a DECLARED workflow (same inputs, same labelling as ``build_declared_recommendation``)."""
    from app.engine.deployment_topology_modeling.service import discover_live

    declared = get_declared_workflow(workflow_id, declaration_path)
    namespace = (namespace or "").strip()
    if not namespace:
        raise ValueError("namespace must be a non-empty string")
    discovery = discover_live(namespace=namespace)
    _require_complete_topology(declared.graph, discovery)
    deployment, _facts = enrich_scheduling_facts(discovery.graph.model_copy(deep=True), namespace)
    overview = failure_overview(
        declared.graph.model_copy(deep=True), deployment,
        EvidenceProvenance(DECLARED_WORKFLOW_SOURCE, namespace, discovery.captured_at, discovery.status, None),
        scheduling_evidence=scheduling_evidence_for(deployment), combination_budget=combination_budget)
    return {**overview, "declaredWorkflow": declared.to_dict()}


def evaluate_recommendation(
    workflow,
    deployment,
    provenance: EvidenceProvenance,
    failed_node: str | None = None,
    *,
    scheduling_evidence: SchedulingEvidence,
    combination_budget: int = DEFAULT_EVALUATION_BUDGET,
    ranking_weights: RankingWeights | None = None,
) -> LiveRecommendation:
    """The pipeline over a workflow and a topology that are already in hand.

    This is the whole evaluation (candidates, feasibility, virtual simulation, Component 3's before/after,
    ranking, pairs) with the fetching taken out, so the same code serves the live route and an offline
    evaluation of captured or controlled artifacts. It reads no cluster and changes neither input. What the
    result may be used for is decided by ``provenance.source`` alone: only the live route passes
    ``LIVE_EVIDENCE_SOURCE``, and an approval is refused for any other source. ``ranking_weights`` overrides
    the experimental default weights of ``ranking.py`` (used by the offline lab for sensitivity checks);
    ``None`` keeps the defaults.
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
    def consider(spec: CandidateSpec) -> None:
        """Evaluate one candidate and record everything the report, the ranking and the pairs need about it."""
        try:
            simulated = evaluate_candidate(deployment, spec, scheduling_evidence)
        except ValueError:
            skipped.append(spec.recommendation_type)  # a type the simulator cannot evaluate yet
            return

        coverage = _coverage(deployment, spec.service)
        after_snapshot, predicted, reduction, predicted_impact = None, before.blast_radius, 0.0, None
        if simulated.feasible:
            try:
                after = predict_failure_impact(workflow, simulated.simulated_deployment, failed_node)
            except ValueError:  # Component 3 could not evaluate the simulated deployment: no impact evidence
                after = None
            if after is not None:
                after_snapshot = _impact_snapshot(after)
                predicted, reduction = after.blast_radius, round(before.blast_radius - after.blast_radius, 2)
                predicted_impact = impact_detail(after)
        candidate_id = f"rec-{workflow_id}-{spec.service}-{spec.recommendation_type}".lower().replace("_", "-")
        feasibility[candidate_id] = simulated.feasibility.to_dict() | {
            "constraintsDigest": scheduling_basis(deployment, [spec.service], model=STRATEGY_MODELS.get(spec.recommendation_type))}
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
            "addedReplicas": list(simulated.added_replicas),
            "predictedImpact": predicted_impact,
        }
        if spec.recommendation_type in PAIRABLE and simulated.feasible:      # verified individually, even when it improves nothing on its own
            members.append(Member(id=candidate_id, service=spec.service, feasibility=simulated.feasibility,
                                  strategy=spec.recommendation_type))
        evaluated.append(EvaluatedCandidate(
            id=candidate_id, type=spec.recommendation_type, service=spec.service, simulation=simulated,
            before=before_snapshot, after=after_snapshot, ready_replicas=ready_replicas[spec.service],
            confidence=coverage, resource_cost=cost,
        ))

    # Plain topology spread for a concentrated service; a replica increase (with its spread) only for a single-replica
    # service, where no change of placement can help; required anti-affinity only for a Deployment that already asks
    # for it softly. Each rule decides from the declared facts, so a strategy is not offered where it cannot differ.
    for spec in generate_candidates(CandidateContextBuilder.build(deployment, before)):
        consider(spec)

    combos = _evaluate_combinations(
        workflow, deployment, failed_node, before, before_snapshot, members, singles_after, outcomes, ready_replicas,
        scheduling_evidence, combination_budget)
    evaluated += combos.candidates
    outcomes.update(combos.outcomes)
    feasibility.update(combos.feasibility)
    candidate_details.update(combos.details)

    weights = ranking_weights or RankingWeights()
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
        details={"currentDeployment": _dump(deployment), "candidates": candidate_details,
                 "baselineImpact": impact_detail(before)},
    )
