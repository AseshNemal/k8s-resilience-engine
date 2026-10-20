"""Portfolio-level failure-impact orchestration for Component 3."""

from collections.abc import Sequence

from app.engine.failure_impact_prediction.blast_radius import (
    calculate_blast_radius,
)
from app.engine.failure_impact_prediction.cross_layer_graph import (
    build_cross_layer_graph,
)
from app.engine.failure_impact_prediction.models import (
    CrossLayerGraph,
    PortfolioFailureImpact,
    PortfolioNodeRisk,
    WorkflowImpactSummary,
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
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph


def _duplicate_workflow_ids(workflows: Sequence[WorkflowGraph]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for workflow in workflows:
        if workflow.workflow_id in seen:
            duplicates.add(workflow.workflow_id)
        seen.add(workflow.workflow_id)
    return sorted(duplicates)


def _evaluate_graphs(
    graphs: tuple[CrossLayerGraph, ...],
    failed_node: str,
) -> tuple[
    tuple[WorkflowSurvivalPrediction, ...],
    tuple[WorkflowImpactSummary, ...],
    tuple[str, ...],
]:
    workflow_predictions: list[WorkflowSurvivalPrediction] = []
    workflow_impacts: list[WorkflowImpactSummary] = []
    failed_services: set[str] = set()

    for graph in graphs:
        simulation = simulate_node_failure(graph, failed_node)
        service_predictions = calculate_service_survival(simulation)
        workflow_prediction = calculate_workflow_survival(
            graph,
            service_predictions,
        )
        unavailable_services = tuple(
            prediction.service
            for prediction in service_predictions
            if not prediction.survives
        )

        workflow_predictions.append(workflow_prediction)
        failed_services.update(unavailable_services)
        workflow_impacts.append(
            WorkflowImpactSummary(
                workflow_id=graph.workflow_id,
                survives=workflow_prediction.survives,
                failed_services=unavailable_services,
                failed_mandatory_services=workflow_prediction.failed_due_to,
            )
        )

    return (
        tuple(workflow_predictions),
        tuple(workflow_impacts),
        tuple(sorted(failed_services)),
    )


def _rank_nodes(
    graphs: tuple[CrossLayerGraph, ...],
) -> tuple[PortfolioNodeRisk, ...]:
    node_risks: list[PortfolioNodeRisk] = []
    for node_name in graphs[0].node_names:
        workflow_predictions, _, _ = _evaluate_graphs(graphs, node_name)
        blast_radius, _ = calculate_blast_radius(workflow_predictions)
        node_risks.append(
            PortfolioNodeRisk(
                node=node_name,
                blast_radius=blast_radius,
                failed_workflows=tuple(
                    prediction.workflow_id
                    for prediction in workflow_predictions
                    if not prediction.survives
                ),
            )
        )

    return tuple(
        sorted(
            node_risks,
            key=lambda risk: (-risk.blast_radius, risk.node),
        )
    )


def predict_portfolio_failure_impact(
    workflows: Sequence[WorkflowGraph],
    deployment: DeploymentGraph,
    failed_node: str,
) -> PortfolioFailureImpact:
    """Evaluate one assumed node failure across several workflow graphs.

    Each workflow is joined independently to the same deployment snapshot, so
    the existing single-workflow simulation and survival rules remain the one
    source of truth. Inputs are treated as immutable.
    """

    if not workflows:
        raise ValueError("At least one workflow is required")

    duplicate_ids = _duplicate_workflow_ids(workflows)
    if duplicate_ids:
        raise ValueError(
            "Duplicate workflow IDs: " + ", ".join(duplicate_ids)
        )

    graphs = tuple(
        build_cross_layer_graph(workflow, deployment)
        for workflow in workflows
    )
    (
        workflow_predictions,
        workflow_impacts,
        failed_services,
    ) = _evaluate_graphs(graphs, failed_node)

    blast_radius, survivability = calculate_blast_radius(
        workflow_predictions
    )

    return PortfolioFailureImpact(
        failed_node=failed_node,
        total_workflows=len(workflow_predictions),
        failed_workflows=tuple(
            prediction.workflow_id
            for prediction in workflow_predictions
            if not prediction.survives
        ),
        surviving_workflows=tuple(
            prediction.workflow_id
            for prediction in workflow_predictions
            if prediction.survives
        ),
        failed_services=failed_services,
        blast_radius=blast_radius,
        survivability=survivability,
        workflow_impacts=workflow_impacts,
        node_risk_ranking=_rank_nodes(graphs),
    )


__all__ = ["predict_portfolio_failure_impact"]
