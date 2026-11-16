"""Read-only placement recommendations when business workflow evidence is unknown.

Uses Member 3 service outcomes and Member 4's existing generator, scheduling checks,
virtual simulator and ranking. No WorkflowGraph or business dependency is synthesized.
This scope cannot authorize Apply or experimental workflow validation.
"""
from types import SimpleNamespace

from app.engine.failure_impact_prediction.service import predict_deployment_failure_impact
from app.engine.recommendation_evaluation_validation.candidate_generation import CandidateContextBuilder, generate_candidates
from app.engine.recommendation_evaluation_validation.live_pipeline import enrich_scheduling_facts, scheduling_evidence_for, RESOURCE_COST
from app.engine.recommendation_evaluation_validation.ranking import (
    ImpactSnapshot, EvaluatedCandidate, rank_candidates, RankingWeights, RESOURCE_FRACTION,
)
from app.engine.recommendation_evaluation_validation.placement_view import placement_view
from app.engine.recommendation_evaluation_validation.service import evaluate_candidate
from app.shared.schemas.failure_impact import RootCause
from app.shared.evidence_errors import EvidenceError

BASIS = "deployment-service-exposure"
NOTE = "Predicted service exposure from observed pod placement, before rescheduling. Workflow survivability and business blast radius are unknown. These suggestions cannot authorize Apply or claim experimental validation."


def _snapshot(report):
    return ImpactSnapshot(failed_node=report.failed_node, blast_radius=report.service_failure_rate,
        survivability=None if report.service_failure_rate is None else 100 - report.service_failure_rate,
        node_blast={n.node: n.service_failure_rate for n in report.node_risk_ranking}, basis=BASIS)


def _cheaper_equal(ranked, rows):
    """The ranked candidate to lead with.

    A candidate is dominated when another ranked one removes at least as much risk (at the assessed node and on
    average) for a strictly lower resource cost. Adding two replicas is not a better answer than adding one when the
    prediction is identical, so the dominating, cheaper change leads. The ranking itself is left as computed.
    """
    tolerance = 1e-9
    pool = [r for r in ranked if r.rank is not None and r.components is not None]
    if not pool:
        return None, None
    first = lead = min(pool, key=lambda r: r.rank)
    while True:
        cheaper = [r for r in pool if r is not lead
                   and RESOURCE_FRACTION[rows[r.id]["resourceCost"]] < RESOURCE_FRACTION[rows[lead.id]["resourceCost"]]
                   and r.components.target_reduction >= lead.components.target_reduction - tolerance
                   and r.components.cluster_reduction >= lead.components.cluster_reduction - tolerance]
        if not cheaper:
            break
        lead = min(cheaper, key=lambda r: r.rank)
    note = None if lead is first else "A costlier change was predicted to remove no more risk, so the cheaper one leads."
    return lead, note


def build_deployment_recommendations(discovery, failed_node=None):
    if discovery.status == "partial" or any(not e.complete for e in discovery.service_evidence.values()):
        raise EvidenceError("TOPOLOGY_INCOMPLETE", "Complete placement is required before simulating deployment recommendations.",
                            http_status=409, component="recommendation")
    deployment, facts = enrich_scheduling_facts(discovery.graph.model_copy(deep=True), discovery.namespace)
    scheduling = scheduling_evidence_for(deployment)
    if not deployment.nodes:
        raise EvidenceError("TOPOLOGY_INCOMPLETE", "No worker placement was observed.", http_status=409, component="recommendation")
    report = predict_deployment_failure_impact(deployment, deployment.nodes[0].name)
    nodes_by_risk = sorted(report.node_risk_ranking, key=lambda n: (-(n.service_failure_rate or 0), n.node))
    worst = failed_node if failed_node in {n.name for n in deployment.nodes} else nodes_by_risk[0].node
    report = predict_deployment_failure_impact(deployment, worst)
    # The generator consumes root-cause facts, not a made-up workflow or a replacement failure algorithm.
    causes = []
    for node in sorted(n.name for n in deployment.nodes):
        impact = predict_deployment_failure_impact(deployment, node)
        causes.extend(RootCause(type="REPLICA_CONCENTRATION", service=s.service, failure_domain=node,
                               concentration=s.lost_ready / s.ready_before)
                      for s in impact.service_impacts if s.outcome == "failed")
    specs = generate_candidates(CandidateContextBuilder.build(deployment, SimpleNamespace(root_causes=causes)))
    evaluated, rows = [], {}
    for spec in specs:
        identity = f"deployment-{spec.service}-{spec.recommendation_type}".lower().replace("_", "-")
        simulated = evaluate_candidate(deployment, spec, scheduling)
        service = next(s for s in deployment.services if s.name == spec.service)
        after = predict_deployment_failure_impact(simulated.simulated_deployment, worst) if simulated.feasible else None
        cost = RESOURCE_COST.get(spec.recommendation_type, "UNKNOWN")
        ready = sum(p.ready for p in service.pods)
        coverage = min(1.0, len(service.pods) / service.declared_replicas) if service.declared_replicas > 0 else 0.0
        evaluated.append(EvaluatedCandidate(id=identity, type=spec.recommendation_type, service=spec.service,
            simulation=simulated, before=_snapshot(report), after=_snapshot(after) if after else None,
            ready_replicas=ready, confidence=coverage, resource_cost=cost))
        rows[identity] = {"id": identity, "type": spec.recommendation_type, "service": spec.service,
            "feasible": simulated.feasible, "feasibility": simulated.feasibility.to_dict(),
            "reason": simulated.feasibility_reason, "rootCause": spec.reason, "resourceCost": cost,
            "predictedServiceFailureRateBefore": report.service_failure_rate,
            "predictedServiceFailureRateAfter": after.service_failure_rate if after else None,
            "worstCaseBefore": max((n.service_failure_rate for n in report.node_risk_ranking if n.service_failure_rate is not None), default=None),
            "worstCaseAfter": max((n.service_failure_rate for n in after.node_risk_ranking if n.service_failure_rate is not None), default=None) if after else None,
            "afterPlacement": placement_view(simulated.simulated_deployment.model_dump(by_alias=True)) if simulated.feasible and simulated.simulated_deployment else None,
            "approvalAllowed": False, "workflowSurvival": None, "workflowBlastRadius": None}
    ranking = rank_candidates(evaluated, RankingWeights())
    lead, lead_note = _cheaper_equal(ranking, rows)
    recommendations = [{**rows[r.id], "ranking": r.to_dict(),
        "averageServiceExposureBefore": r.components.mean_blast_before if r.components else None,
        "averageServiceExposureAfter": r.components.mean_blast_after if r.components else None} for r in ranking]
    return {"scope": "deployment", "basis": BASIS, "namespace": discovery.namespace, "capturedAt": discovery.captured_at,
            "isPrediction": True, "workflowSurvival": None, "workflowBlastRadius": None, "approvalAllowed": False,
            "note": NOTE, "failedNode": worst, "nodes": [n.model_dump(by_alias=True) for n in report.node_risk_ranking],
            "currentDeployment": deployment.model_dump(by_alias=True), "recommendations": recommendations,
            "selectedCandidateId": lead.id if lead else None, "selectionNote": lead_note,
            "kubernetesVersion": facts.get("kubernetesVersion")}
