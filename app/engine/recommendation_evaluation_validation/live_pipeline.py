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
* ``score`` is ``risk_reduction / 100 * confidence``, so a large predicted gain from thin evidence ranks lower.
"""
from typing import Any

from app.engine.failure_impact_prediction.runtime_provider import get_runtime_component_inputs
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.approval import LIVE_EVIDENCE_SOURCE, LiveRecommendation
from app.engine.recommendation_evaluation_validation.candidate_generation import (
    CandidateContextBuilder,
    generate_candidates,
)
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    UNSCHEDULED_NODE,
    SchedulingEvidence,
)
from app.engine.recommendation_evaluation_validation.service import evaluate_candidate
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.recommendation import RecommendationCandidate, RecommendationReport

COMPONENT = "recommendation"

# Applying a topology spread constraint restarts the pods that have to move, but adds no replicas.
RESOURCE_COST = {"TOPOLOGY_SPREAD": "LOW"}

CONFIDENCE_MEANING = (
    "Evidence coverage: the share of the service's declared replicas whose placement was observed. "
    "It is not the accuracy of the prediction."
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


def build_live_recommendation(
    workflow_id: str,
    failed_node: str | None = None,
    namespace: str = "default",
) -> LiveRecommendation:
    """The recommendation report for one frozen workflow on the connected cluster, with its evidence.

    ``failed_node`` is the worker to protect against. When it is not given, the worker whose loss would hurt
    most is used, and the evidence says so. Every failure to read the cluster or the workflow is the coded
    error the other components already raise; none of them is replaced by a fixture.
    """
    inputs = get_runtime_component_inputs(workflow_id, namespace)
    workflow, deployment = inputs.workflow, inputs.deployment

    chosen_by = "requested"
    if not failed_node:
        failed_node, chosen_by = _riskiest_node(workflow, deployment), "highest blast radius"
    try:
        before = predict_failure_impact(workflow, deployment, failed_node)
    except ValueError as error:  # an unknown failed node
        raise _invalid(str(error)) from error

    candidates: list[RecommendationCandidate] = []
    skipped: list[str] = []
    feasibility: dict[str, Any] = {}
    scheduling_evidence = scheduling_evidence_for(deployment)
    for spec in generate_candidates(CandidateContextBuilder.build(deployment, before)):
        try:
            simulated = evaluate_candidate(deployment, spec, scheduling_evidence)
        except ValueError:
            skipped.append(spec.recommendation_type)  # a type the simulator cannot evaluate yet
            continue

        coverage = _coverage(deployment, spec.service)
        if simulated.feasible:
            after = predict_failure_impact(workflow, simulated.simulated_deployment, failed_node)
            predicted, reduction = after.blast_radius, round(before.blast_radius - after.blast_radius, 2)
        else:
            predicted, reduction = before.blast_radius, 0.0
        candidate_id = f"rec-{workflow_id}-{spec.service}-{spec.recommendation_type}".lower().replace("_", "-")
        feasibility[candidate_id] = simulated.feasibility.to_dict()
        candidates.append(RecommendationCandidate(
            id=candidate_id,
            type=spec.recommendation_type,
            service=spec.service,
            feasible=simulated.feasible,
            predicted_blast_radius=predicted,
            risk_reduction=reduction,
            resource_cost=RESOURCE_COST.get(spec.recommendation_type, "UNKNOWN"),
            confidence=coverage,
            score=round(max(reduction, 0.0) / 100.0 * coverage, 4),
        ))

    candidates.sort(key=lambda c: (-c.score, c.service, c.type))
    best = next((c for c in candidates if c.feasible and c.risk_reduction > 0), None)
    report = RecommendationReport(
        workflow_id=workflow.workflow_id,
        recommendations=candidates,
        selected_recommendation_id=best.id if best else None,
    )
    evidence: dict[str, Any] = {
        "source": LIVE_EVIDENCE_SOURCE,
        "topologyCapturedAt": inputs.topology_captured_at,
        "topologyStatus": inputs.topology_status,
        "namespace": inputs.namespace,
        "workflowId": workflow.workflow_id,
        "workflowFreezeId": inputs.workflow_freeze_id,
        "failedNode": failed_node,
        "failedNodeChosen": chosen_by,
        "blastRadiusBefore": before.blast_radius,
        "confidenceMeaning": CONFIDENCE_MEANING,
        "skippedUnsupportedTypes": sorted(set(skipped)),
        "feasibility": feasibility,
        "isPrediction": True,
    }
    return LiveRecommendation(report=report, evidence=evidence)
