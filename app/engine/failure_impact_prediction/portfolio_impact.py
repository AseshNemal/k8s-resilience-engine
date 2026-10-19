"""Portfolio-level failure-impact orchestration for Component 3."""

from collections.abc import Sequence

from app.engine.failure_impact_prediction.blast_radius import (
    calculate_blast_radius,
)
from app.engine.failure_impact_prediction.cross_layer_graph import (
    build_cross_layer_graph,
)
from app.engine.failure_impact_prediction.models import (
    PortfolioFailureImpact,
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

    workflow_predictions: list[WorkflowSurvivalPrediction] = []
    workflow_impacts: list[WorkflowImpactSummary] = []
    failed_services: set[str] = set()

    for workflow in workflows:
        graph = build_cross_layer_graph(workflow, deployment)
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
                workflow_id=workflow.workflow_id,
                survives=workflow_prediction.survives,
                failed_services=unavailable_services,
                failed_mandatory_services=workflow_prediction.failed_due_to,
            )
        )

    blast_radius, survivability = calculate_blast_radius(
        tuple(workflow_predictions)
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
        failed_services=tuple(sorted(failed_services)),
        blast_radius=blast_radius,
        survivability=survivability,
        workflow_impacts=tuple(workflow_impacts),
    )


__all__ = ["predict_portfolio_failure_impact"]
