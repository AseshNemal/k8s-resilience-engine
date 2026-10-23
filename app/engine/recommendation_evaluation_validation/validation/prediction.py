"""The simulation's predicted outcome for one candidate, taken from an evaluation and fixed before anything is applied."""

from collections import Counter

from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.approval import LiveRecommendation
from app.engine.recommendation_evaluation_validation.approval.eligibility import proposal_fingerprint
from app.engine.recommendation_evaluation_validation.validation.models import (
    PredictedOutcome,
    ValidationRefusedError,
)
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph


def _graph(raw, what: str) -> DeploymentGraph:
    if not isinstance(raw, dict):
        raise ValidationRefusedError("PREDICTION_UNAVAILABLE", f"The evaluation does not carry {what}.")
    return DeploymentGraph.model_validate(raw)


def predicted_placement(graph: DeploymentGraph, services: list[str]) -> dict[str, dict[str, int]]:
    """Per service, how many ready replicas the model puts on each node."""
    wanted = set(services)
    return {s.name: dict(sorted(Counter(p.node for p in s.pods if p.ready).items()))
            for s in graph.services if s.name in wanted}


def predict_outcome(workflow: WorkflowGraph, live: LiveRecommendation, recommendation_id: str) -> PredictedOutcome:
    """Component 3's before/after prediction for the candidate, on the current and the virtual topology.

    Both predictions go through Component 3's public ``predict_failure_impact``; nothing is recomputed here. Raises
    ``ValidationRefusedError`` when the evaluation does not hold a simulated placement for the candidate (an
    infeasible or unverified one has none).
    """
    detail = ((live.details or {}).get("candidates") or {}).get(recommendation_id)
    if not isinstance(detail, dict):
        raise ValidationRefusedError("PREDICTION_UNAVAILABLE", f"{recommendation_id} is not in the evaluation.")
    failed_node = (live.evidence or {}).get("failedNode")
    fingerprint = proposal_fingerprint(live, recommendation_id)
    if not failed_node or fingerprint is None:
        raise ValidationRefusedError("PREDICTION_UNAVAILABLE", "The evaluation records no failed node or proposal plan.")
    current = _graph((live.details or {}).get("currentDeployment"), "the current topology")
    simulated = _graph(detail.get("simulatedDeployment"), "a simulated placement for this candidate")
    services = list(detail.get("services") or [detail.get("service")])
    return PredictedOutcome(
        recommendation_id=recommendation_id, workflow_id=workflow.workflow_id, failed_node=failed_node,
        fingerprint=fingerprint, before=predict_failure_impact(workflow, current, failed_node),
        after=predict_failure_impact(workflow, simulated, failed_node),
        placement=predicted_placement(simulated, services),
    )
