"""Workflow blast-radius and risky-node ranking calculations."""

from app.engine.failure_impact_prediction.models import (
    CrossLayerGraph,
    WorkflowSurvivalPrediction,
)
from app.engine.failure_impact_prediction.node_failure_simulator import (
    simulate_node_failure,
)
from app.engine.failure_impact_prediction.service_survival import (
    calculate_service_survival,
)
from app.engine.failure_impact_prediction.workflow_survival import (
    calculate_workflow_survival,
)
from app.shared.schemas.failure_impact import NodeRisk


def calculate_blast_radius(
    workflows: tuple[WorkflowSurvivalPrediction, ...],
) -> tuple[float, float]:
    """Return blast radius and survivability as percentages from 0 to 100."""

    if not workflows:
        raise ValueError("At least one workflow is required for blast-radius calculation")
    failed_count = sum(not workflow.survives for workflow in workflows)
    blast_radius = round((failed_count / len(workflows)) * 100.0, 2)
    return blast_radius, round(100.0 - blast_radius, 2)


def calculate_node_risk_ranking(
    graph: CrossLayerGraph,
) -> list[NodeRisk]:
    """Simulate every worker node and rank by workflow-level impact."""

    risks: list[NodeRisk] = []
    for node_name in graph.node_names:
        simulation = simulate_node_failure(graph, node_name)
        service_predictions = calculate_service_survival(simulation)
        workflow_prediction = calculate_workflow_survival(
            graph,
            service_predictions,
        )
        blast_radius, _ = calculate_blast_radius((workflow_prediction,))
        risks.append(NodeRisk(node=node_name, blast_radius=blast_radius))

    return sorted(risks, key=lambda risk: (-risk.blast_radius, risk.node))
