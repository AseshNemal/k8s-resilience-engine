"""One application assessment: topology, every worker-node failure, and the leading feasible change.

Member 2 supplies the snapshot, Member 1 the workflows that were actually reviewed, Member 3 the
failure picture, and Member 4 the candidates. This module does not invent a workflow, apply a
change, or stop a node. Recommendations are computed on the same discovery object the failure
picture used.
"""
from typing import Any

from app.engine.failure_impact_prediction.service import load_application_snapshot
from app.engine.recommendation_evaluation_validation.placement_view import placement_view
from app.engine.recommendation_evaluation_validation.portfolio import PORTFOLIO_ANALYSIS_SOURCE
from app.shared.evidence_errors import EvidenceError

TITLES = {
    "TOPOLOGY_SPREAD": "Spread replicas across worker nodes",
    "POD_ANTI_AFFINITY": "Keep replicas on different worker nodes",
    "REPLICA_INCREASE": "Add one replica and spread it",
    "REPLICA_INCREASE_ANTI_AFFINITY": "Add one replica and keep replicas apart",
    "REPLICA_INCREASE_PLUS_2": "Add two replicas and spread them",
    "COMBINATION": "Apply the combined placement changes",
}
COST = {
    "LOW": "No extra replicas. The Deployment still rolls out.",
    "MEDIUM": "One extra replica, plus a rollout.",
    "HIGH": "Two extra replicas, plus a rollout.",
}
LEVELS = {
    "workflow-lost": "critical",
    "service-down": "high",
    "degraded": "moderate",
    "no-impact": "none",
    "no-pods": "none",
}
PREDICTION = "Predicted from the placement read just now. This is not a measured outage, and nothing was changed."


def _number(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _title(kind: str | None, service: str | None) -> str:
    name = TITLES.get(kind or "", "Placement change")
    return f"{name} for {service}" if service else name


def _level(blast: float | None, verdict: str | None) -> str:
    if blast is None:
        return LEVELS.get(verdict or "", "unknown")
    if blast <= 0:
        return "none"
    if blast >= 75:
        return "critical"
    if blast >= 50:
        return "high"
    if blast >= 25:
        return "moderate"
    return "low"


def _risk(overview: dict) -> dict[str, Any]:
    nodes = overview.get("nodes") or []
    if not nodes:
        return {"level": "unknown", "headline": "No worker node was assessed.", "worstNode": None,
                "blastRadius": None, "scope": overview.get("scope")}
    worst = nodes[0]
    blast = _number(worst.get("blastRadius")) if overview.get("scope") == "workflow" else None
    return {
        "level": _level(blast, worst.get("verdict")),
        "headline": worst.get("headline") or "The leading failure was not described.",
        "worstNode": worst.get("node"),
        "blastRadius": blast,
        "verdict": worst.get("verdict"),
        "scope": overview.get("scope") or "service",
    }


def _services(overview: dict) -> list[dict[str, Any]]:
    exposed = {}
    for node in overview.get("nodes") or []:
        for name in node.get("servicesDown") or []:
            exposed.setdefault(name, {"service": name, "lostOn": [], "degradedOn": []})["lostOn"].append(node["node"])
        for name in node.get("servicesDegraded") or []:
            exposed.setdefault(name, {"service": name, "lostOn": [], "degradedOn": []})["degradedOn"].append(node["node"])
    by_name = {row["service"]: row for row in overview.get("services") or []}
    rows = []
    for name, item in sorted(exposed.items()):
        placement = by_name.get(name) or {}
        rows.append({**item, "readyReplicas": placement.get("readyReplicas"), "declaredReplicas": placement.get("declaredReplicas"),
                     "singleNode": placement.get("singleNode"), "nodes": placement.get("nodes") or []})
    return rows


def _card_from_live(candidate: dict) -> dict[str, Any]:
    predicted = candidate.get("predicted") or {}
    before, after = _number(predicted.get("blastRadiusBefore")), _number(predicted.get("blastRadiusAfter"))
    reduction = _number(predicted.get("riskReduction"))
    benefit = None
    if reduction is not None and candidate.get("feasible"):
        benefit = f"Predicted workflow loss falls by {reduction:g} percentage points."
    elif before is not None and after is not None and candidate.get("feasible"):
        benefit = f"Predicted blast radius moves from {before:g}% to {after:g}%."
    notes = candidate.get("notes") or []
    feasibility = (candidate.get("feasibility") or {}).get("explanation")
    return {
        "id": candidate.get("id"),
        "title": _title(candidate.get("type"), candidate.get("service")),
        "service": candidate.get("service"),
        "type": candidate.get("type"),
        "why": candidate.get("selectionJustification") or candidate.get("rootCause"),
        "benefit": benefit,
        "cost": (candidate.get("cost") or {}).get("resource"),
        "costDetail": COST.get((candidate.get("cost") or {}).get("resource"), "Resource cost was not reported."),
        "limitation": notes[0] if notes else feasibility,
        "feasible": bool(candidate.get("feasible")),
        "selected": bool(candidate.get("selected")),
        "metric": {"label": "Predicted workflow loss, worst case", "before": before, "after": after}
        if before is not None and after is not None else None,
        "afterPlacement": placement_view(candidate.get("simulatedDeployment")) if candidate.get("feasible") else None,
    }


def _pct(value: float) -> str:
    """Whole percent, the same rounding the Recommendations page uses for these exposures."""
    return f"{value:.0f}"


def _move(label: str, before: float | None, after: float | None) -> str | None:
    if before is None or after is None or abs(before - after) < 0.05:
        return None
    return f"{label} moves from {_pct(before)}% to {_pct(after)}%."


def _deployment_metric(row: dict) -> dict[str, Any] | None:
    before, after = _number(row.get("averageServiceExposureBefore")), _number(row.get("averageServiceExposureAfter"))
    if not row.get("feasible") or before is None or after is None:
        return None
    return {"label": "Services lost when a worker stops, on average", "before": before, "after": after}


def _card_from_deployment(row: dict) -> dict[str, Any]:
    benefit = None
    if row.get("feasible"):
        worst_before, worst_after = _number(row.get("worstCaseBefore")), _number(row.get("worstCaseAfter"))
        parts = [text for text in (
            _move("Average service exposure across worker-node failures", _number(row.get("averageServiceExposureBefore")), _number(row.get("averageServiceExposureAfter"))),
            _move("Worst-case service exposure", worst_before, worst_after),
        ) if text]
        if not parts and worst_before is not None:
            parts.append(f"Worst-case service exposure stays {_pct(worst_before)}%.")
        elif worst_before is not None and worst_after is not None and abs(worst_before - worst_after) < 0.05 and parts:
            parts.append(f"Worst-case service exposure stays {_pct(worst_before)}%.")
        benefit = " ".join(parts) if parts else None
    ranking = row.get("ranking") or {}
    detail = ranking.get("detail") or ""
    why = row.get("rootCause") if not detail or detail.lower().startswith("ranked") else detail
    return {
        "id": row.get("id"),
        "title": _title(row.get("type"), row.get("service")),
        "service": row.get("service"),
        "type": row.get("type"),
        "why": why or row.get("reason"),
        "benefit": benefit,
        "cost": row.get("resourceCost"),
        "costDetail": COST.get(row.get("resourceCost"), "Resource cost was not reported."),
        "limitation": row.get("reason") if not row.get("feasible") else "This suggestion cannot be applied from the assessment. Workflow survival stays unknown.",
        "feasible": bool(row.get("feasible")),
        "selected": False,
        "metric": _deployment_metric(row),
        "afterPlacement": row.get("afterPlacement") if row.get("feasible") else None,
    }


COST_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _cost_rank(card: dict) -> int:
    return COST_ORDER.get(card.get("cost"), 3)


def _pack(cards: list[dict], *, mode: str, failed_node: str | None, note: str, approval_allowed: bool,
          exposed: dict[str, str] | None = None) -> dict[str, Any]:
    """Lead change, the same service's other options, and (service scope) the other exposed services' cheapest fix.

    ``exposed`` maps each service that is lost when one worker stops to that worker. A change that fixes one of
    them does not fix the rest, and saying so is part of the answer.
    """
    selected = next((card for card in cards if card["selected"] and card["feasible"]), None)
    if selected is None:
        selected = next((card for card in cards if card["feasible"]), None)
    feasible = [card for card in cards if card["feasible"] and card is not selected]
    alternatives = [card for card in feasible
                    if selected and card["service"] == selected["service"] and _cost_rank(card) <= _cost_rank(selected)][:3]
    follow_ups = []
    if selected and exposed:
        for service, node in sorted(exposed.items()):
            if service == selected["service"]:
                continue
            options = [card for card in feasible if card["service"] == service]
            if options:
                follow_ups.append({**min(options, key=_cost_rank), "node": node})
    blocked = [{"title": card["title"], "limitation": card["limitation"]}
               for card in cards if not card["feasible"] and card["limitation"]][:3]
    if selected is None and blocked:
        note = f"{note} {blocked[0]['title']}: {blocked[0]['limitation']}"
    return {
        "available": selected is not None,
        "mode": mode,
        "approvalAllowed": approval_allowed,
        "failedNode": failed_node,
        "best": selected,
        "alternatives": alternatives,
        "followUps": follow_ups,
        "blocked": blocked,
        "considered": len(cards),
        "note": note,
    }


def _workflow_recommendation(snapshot) -> dict[str, Any]:
    from app.engine.recommendation_evaluation_validation.explanation import describe_live_recommendation
    from app.engine.recommendation_evaluation_validation.live_pipeline import (
        EvidenceProvenance,
        enrich_scheduling_facts,
        evaluate_recommendation,
        scheduling_evidence_for,
    )
    from app.engine.recommendation_evaluation_validation.portfolio import WorkflowPortfolio

    overview = snapshot.public
    graphs = snapshot.evaluated_workflows
    subject = WorkflowPortfolio(tuple(graphs)) if len(graphs) > 1 else graphs[0]
    deployment, _facts = enrich_scheduling_facts(snapshot.discovery.graph.model_copy(deep=True), overview["namespace"])
    live = evaluate_recommendation(
        subject, deployment,
        EvidenceProvenance(PORTFOLIO_ANALYSIS_SOURCE, overview["namespace"], overview.get("capturedAt"),
                           (overview.get("topology") or {}).get("status"), None),
        scheduling_evidence=scheduling_evidence_for(deployment),
    )
    described = describe_live_recommendation(live)
    cards = [_card_from_live(candidate) for candidate in described.get("candidates") or []]
    evidence = described.get("evidence") or {}
    return _pack(
        cards, mode="workflow", failed_node=evidence.get("failedNode"), approval_allowed=False,
        note="Workflow impact is a prediction on the reviewed definitions. Approval is a separate step on the review page, which checks the change again. Nothing here is applied.",
    )


def _deployment_recommendation(snapshot, failed_node: str | None = None) -> dict[str, Any]:
    from app.engine.recommendation_evaluation_validation.deployment_recommendations import (
        NOTE,
        build_deployment_recommendations,
    )

    findings = build_deployment_recommendations(snapshot.discovery, failed_node)
    cards = [_card_from_deployment(row) for row in findings.get("recommendations") or []]
    selected = findings.get("selectedCandidateId")
    for card in cards:
        card["selected"] = card["id"] == selected
    exposed = {name: node["node"] for node in snapshot.public.get("nodes") or []
               for name in node.get("servicesDown") or []}
    return _pack(cards, mode="deployment", failed_node=findings.get("failedNode"), approval_allowed=False, note=NOTE,
                 exposed=exposed)


def _recommend(snapshot) -> dict[str, Any]:
    overview = snapshot.public
    topology = overview.get("topology") or {}
    if not topology.get("complete"):
        missing = ", ".join(topology.get("incompleteServices") or []) or "part of the application"
        return {"available": False, "mode": None, "approvalAllowed": False, "best": None, "alternatives": [],
                "note": f"Placement is incomplete for {missing}, so no change is proposed."}
    try:
        if snapshot.evaluated_workflows:
            return _workflow_recommendation(snapshot)
        if overview.get("scope") == "service":
            return _deployment_recommendation(snapshot, (overview.get("nodes") or [{}])[0].get("node"))
    except EvidenceError as error:
        return {"available": False, "mode": overview.get("scope"), "approvalAllowed": False, "best": None,
                "alternatives": [], "note": error.message, "code": error.code}
    return {"available": False, "mode": overview.get("scope"), "approvalAllowed": False, "best": None,
            "alternatives": [], "note": "No feasible change was produced for this application."}


def compose_assessment(snapshot) -> dict[str, Any]:
    """Turn one application snapshot into the product answer. Does not read the cluster again."""
    overview = snapshot.public
    if overview.get("state") != "ready":
        return {**overview, "readOnly": True, "isPrediction": True, "risk": None, "vulnerableServices": [],
                "recommendation": None, "continuation": None}
    recommendation = _recommend(snapshot)
    risk = _risk(overview)
    basis = overview.get("basis") or {}
    best = recommendation.get("best") or {}
    continuation = {
        "label": ("Review this change" if recommendation.get("mode") == "workflow" else "Compare all options")
        if recommendation.get("available") else "Open recommendations",
        "href": "/components/recommendations",
        "namespace": overview.get("namespace"),
        "failedNode": recommendation.get("failedNode") or risk.get("worstNode"),
        "fingerprint": (overview.get("cluster") or {}).get("fingerprint"),
        "note": "Opens Recommendations for this application, node, and change. That page checks it again. Nothing is applied until a person approves it.",
        "candidate": {"id": best.get("id"), "service": best.get("service"), "type": best.get("type")} if best.get("service") and best.get("type") else None,
    }
    nodes = [{
        "node": row.get("node"), "rank": row.get("rank"), "verdict": row.get("verdict"), "headline": row.get("headline"),
        "blastRadius": row.get("blastRadius") if overview.get("scope") == "workflow" else None,
        "servicesDown": row.get("servicesDown") or [], "servicesDegraded": row.get("servicesDegraded") or [],
        "workflowsLost": row.get("workflowsLost"), "workflowsEvaluated": row.get("workflowsEvaluated"),
        "workflows": [{"workflowId": flow.get("workflowId"), "name": flow.get("name"), "survives": flow.get("survives"),
                       "failedMandatoryServices": flow.get("failedMandatoryServices") or []}
                      for flow in row.get("workflows") or []] if overview.get("scope") == "workflow" else [],
    } for row in overview.get("nodes") or []]
    return {
        "readOnly": True,
        "isPrediction": True,
        "state": "ready",
        "namespace": overview.get("namespace"),
        "capturedAt": overview.get("capturedAt"),
        "cluster": overview.get("cluster"),
        "namespaces": overview.get("namespaces") or [],
        "applications": overview.get("applications") or [],
        "evidence": {
            "workflow": basis.get("kind") or "none",
            "workflowText": basis.get("text"),
            "workflowsEvaluated": overview.get("workflowsEvaluated") or [],
            "workflowsAlreadyBroken": overview.get("workflowsAlreadyBroken") or [],
            "workflowsNotEvaluated": overview.get("workflowsNotEvaluated") or [],
            "problems": basis.get("problems") or [],
            "topologyComplete": bool((overview.get("topology") or {}).get("complete")),
            "prediction": PREDICTION,
        },
        "risk": risk,
        "nodes": nodes,
        "vulnerableServices": _services(overview),
        "placement": [{"service": row.get("service"), "readyReplicas": row.get("readyReplicas"),
                       "declaredReplicas": row.get("declaredReplicas"), "nodes": row.get("nodes") or []}
                      for row in overview.get("services") or []],
        "singlePointsOfFailure": overview.get("singlePointsOfFailure") or [],
        "recommendation": recommendation,
        "continuation": continuation,
        "selectedChange": best.get("title"),
    }


def build_product_assessment(namespace: str | None = None) -> dict[str, Any]:
    """Read the connected cluster once and return the product assessment for one application."""
    try:
        snapshot = load_application_snapshot(namespace)
    except ValueError as error:
        raise EvidenceError("ASSESSMENT_UNAVAILABLE", str(error), http_status=409, component="assessment") from error
    return compose_assessment(snapshot)
