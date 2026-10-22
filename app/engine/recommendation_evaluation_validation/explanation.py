"""What the Recommendations page shows for one live recommendation, assembled from what the engine computed.

Nothing in here calculates a research value. Every number and sentence about a candidate comes from the
``LiveRecommendation`` that ``live_pipeline`` produced: the report (feasibility, predicted blast radius, risk
reduction, cost, confidence, score), the evidence (feasibility findings, the ranking entry with its exclusion
reason), and the display details (root cause, current placement, simulated placement). The only thing added is
Member 4's own eligibility verdict (``approval.eligibility``), so that "ready for approval" is decided by the same
rule the approval gate enforces, never by the browser.

Two states are kept apart on purpose, because they are easy to confuse and one of them is dangerous:

* ``NO_CANDIDATES``: the evidence was complete and the engine generated no candidate change. That is NOT a finding
  that the application is resilient. The engine only considers the change types it models (today: spreading the
  replicas of a service that is concentrated on one node), so an application it has nothing to say about may still
  be fragile.
* ``CANDIDATES_NOT_READY``: candidates were evaluated, but none can be put to a developer for approval, with the
  reason for each.

Nothing here is applied, and nothing here writes to a cluster.
"""
from typing import Any

from app.engine.recommendation_evaluation_validation.approval import LiveRecommendation
from app.engine.recommendation_evaluation_validation.approval.eligibility import evaluate_eligibility

TOPOLOGY_SPREAD = "TOPOLOGY_SPREAD"
COMBINATION = "COMBINATION"

RECOMMENDED_CHANGE = {
    TOPOLOGY_SPREAD: (
        "Add a topologySpreadConstraint on kubernetes.io/hostname (maxSkew 1, whenUnsatisfiable DoNotSchedule) to "
        "the Deployment's pod template, so that its replicas are placed on at least two different worker nodes. "
        "The edit changes the pod template, so the Deployment rolls out."
    ),
    COMBINATION: "Apply the topology spread change to each of the listed services together, as one proposal.",
}

COST_NOTE = {
    "LOW": (
        "Adds no replicas, so no extra CPU or memory is requested. The pod-template edit rolls out every pod of the "
        "Deployment; that rollout disruption is not modelled."
    ),
}

SINGLE_REPLICA_NOTE = (
    "This service runs a single replica. A spread constraint cannot make one replica survive the loss of the node "
    "it runs on, and the engine did not produce a feasible change that does."
)

YAML_NOT_AVAILABLE = (
    "No Kubernetes YAML generator exists in the engine yet, so no proposal text is shown. Nothing is generated, "
    "invented or applied."
)

VALIDATION_NOT_AVAILABLE = (
    "Validation compares a prediction with what was measured after an approved change was applied. Nothing has "
    "been applied, so there is nothing to validate."
)

NO_CANDIDATES_MESSAGE = (
    "The evidence is complete, but the engine generated no candidate change for this workflow and this failure. "
    "This is not a finding that the application is resilient: only the change types the engine models were "
    "considered (today, spreading the replicas of a service that is concentrated on one node)."
)


def _feasibility(entry: dict[str, Any] | None, reason: str | None) -> dict[str, Any]:
    entry = entry or {}
    return {
        "status": entry.get("status"),
        "explanation": reason or None,
        "findings": entry.get("findings", []),
        "missingEvidence": entry.get("missingEvidence", []),
    }


def _selection_text(candidate_id: str, ranking: dict[str, Any] | None, selected: str | None) -> str:
    """Why this candidate is or is not the selection, in the ranking's own words."""
    if not ranking:
        return "The ranking did not report this candidate."
    if candidate_id == selected:
        return f"Selected: top-ranked feasible candidate (rank 1, score {ranking.get('score')}). {ranking.get('detail', '')}".strip()
    if ranking.get("rank") is not None:
        return f"Ranked {ranking['rank']}; not the selection. {ranking.get('detail', '')}".strip()
    excluded = ranking.get("excluded")
    return f"Not ranked ({excluded}). {ranking.get('detail', '')}".strip() if excluded else (ranking.get("detail") or "Not ranked.")


def describe_live_recommendation(live: LiveRecommendation) -> dict[str, Any]:
    """The live recommendation as the page shows it. Pure: it reads ``live`` and changes nothing."""
    report, evidence, details = live.report, live.evidence, live.details or {}
    per_candidate = details.get("candidates", {})
    ranking_entries = (evidence.get("ranking") or {}).get("candidates", {})
    feasibility = evidence.get("feasibility", {})
    selected = report.selected_recommendation_id
    before = evidence.get("blastRadiusBefore")

    candidates: list[dict[str, Any]] = []
    for item in report.recommendations:
        detail = per_candidate.get(item.id, {})
        standing = evaluate_eligibility(live, item.id)
        placement = detail.get("currentPlacement", {})
        notes: list[str] = []
        if item.type == TOPOLOGY_SPREAD and detail.get("declaredReplicas") == 1:
            notes.append(SINGLE_REPLICA_NOTE)
        candidates.append({
            "id": item.id,
            "type": item.type,
            "service": item.service,
            "rootCause": detail.get("rootCause"),
            "recommendedChange": RECOMMENDED_CHANGE.get(item.type),
            "feasible": item.feasible,
            "feasibility": _feasibility(feasibility.get(item.id), detail.get("feasibilityReason")),
            "currentPlacement": placement,
            "declaredReplicas": detail.get("declaredReplicas"),
            "readyReplicas": detail.get("readyReplicas"),
            "podsToRecreate": detail.get("podsToRecreate", []),
            "predicted": {
                "blastRadiusBefore": before,
                "blastRadiusAfter": item.predicted_blast_radius,
                "riskReduction": item.risk_reduction,
            },
            "cost": {"resource": item.resource_cost, "note": COST_NOTE.get(item.resource_cost),
                     "configuration": f"{len(detail.get('services') or [item.service])} Deployment pod-template edit(s)"},
            "confidence": item.confidence,
            "score": item.score,
            "ranking": ranking_entries.get(item.id),
            "selected": item.id == selected,
            "selectionJustification": _selection_text(item.id, ranking_entries.get(item.id), selected),
            "approval": {
                "ready": standing.eligible,
                "reason": None if standing.eligible else standing.reason,
                "requiresJustification": standing.requires_justification,
            },
            "simulatedDeployment": detail.get("simulatedDeployment"),
            "notes": notes,
        })

    ready = [c for c in candidates if c["approval"]["ready"]]
    if not candidates:
        outcome = {"state": "NO_CANDIDATES", "message": NO_CANDIDATES_MESSAGE}
    elif not ready:
        outcome = {"state": "CANDIDATES_NOT_READY",
                   "message": f"{len(candidates)} candidate(s) were evaluated and none can be approved; the reason for each is shown."}
    else:
        outcome = {"state": "READY", "message": f"{len(ready)} candidate(s) can be put to a developer for approval."}

    return {
        "outcome": outcome,
        "report": report.model_dump(by_alias=True),
        "evidence": {
            "workflowId": evidence.get("workflowId"),
            "workflowFreezeId": evidence.get("workflowFreezeId"),
            "namespace": evidence.get("namespace"),
            "topologyCapturedAt": evidence.get("topologyCapturedAt"),
            "topologyStatus": evidence.get("topologyStatus"),
            "failedNode": evidence.get("failedNode"),
            "failedNodeChosen": evidence.get("failedNodeChosen"),
            "blastRadiusBefore": before,
            "confidenceMeaning": evidence.get("confidenceMeaning"),
            "skippedUnsupportedTypes": evidence.get("skippedUnsupportedTypes", []),
            "isPrediction": True,
        },
        "candidates": candidates,
        "currentDeployment": details.get("currentDeployment"),
        "yaml": {"available": False, "reason": YAML_NOT_AVAILABLE},
        "validation": {"available": False, "reason": VALIDATION_NOT_AVAILABLE},
        "applied": False,
    }
