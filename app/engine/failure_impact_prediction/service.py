"""Public service interface for Component 3 failure-impact prediction."""

from app.engine.failure_impact_prediction.blast_radius import (
    calculate_blast_radius,
    calculate_node_risk_ranking,
)
from app.engine.failure_impact_prediction.cross_layer_graph import (
    build_cross_layer_graph,
)
from app.engine.failure_impact_prediction.fixture_provider import (
    get_current_component_inputs,
    get_demo_deployment,
    get_demo_inputs,
)
from app.engine.failure_impact_prediction.models import CrossLayerGraph
from app.engine.failure_impact_prediction.node_failure_simulator import (
    simulate_node_failure,
)
from app.engine.failure_impact_prediction.portfolio_impact import (
    predict_portfolio_failure_impact,
)
from app.engine.failure_impact_prediction.runtime_provider import (
    RuntimeInputUnavailableError,
    get_runtime_component_inputs,
)
from app.engine.failure_impact_prediction.service_survival import (
    calculate_service_survival,
)
from app.engine.failure_impact_prediction.workflow_survival import (
    calculate_workflow_survival,
)
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import FailureImpactReport, RootCause
from app.shared.schemas.workflow import WorkflowGraph


def build_current_cross_layer_graph() -> CrossLayerGraph:
    """Join current Component 1/2 samples and report unresolved services."""

    workflow, deployment = get_current_component_inputs()
    return build_cross_layer_graph(
        workflow,
        deployment,
        require_complete=False,
    )


def _predict_from_graph(
    graph: CrossLayerGraph,
    failed_node: str,
) -> FailureImpactReport:
    simulation = simulate_node_failure(graph, failed_node)
    service_predictions = calculate_service_survival(simulation)
    workflow_prediction = calculate_workflow_survival(
        graph,
        service_predictions,
    )
    blast_radius, survivability = calculate_blast_radius(
        (workflow_prediction,)
    )

    failed_service_predictions = tuple(
        prediction
        for prediction in service_predictions
        if not prediction.survives
    )
    root_causes: list[RootCause] = []
    for prediction in failed_service_predictions:
        initially_ready = len(prediction.initially_ready_pods)
        concentration = (
            len(prediction.lost_ready_pods) / initially_ready
            if initially_ready
            else None
        )
        root_causes.append(
            RootCause(
                type=(
                    "REPLICA_CONCENTRATION"
                    if prediction.caused_by_failed_node
                    else "PREEXISTING_UNAVAILABILITY"
                ),
                service=prediction.service,
                failure_domain=failed_node,
                concentration=concentration,
            )
        )

    return FailureImpactReport(
        workflow_id=graph.workflow_id,
        failed_node=failed_node,
        blast_radius=blast_radius,
        survivability=survivability,
        failed_services=[
            prediction.service for prediction in failed_service_predictions
        ],
        failed_workflows=(
            [workflow_prediction.workflow_id]
            if not workflow_prediction.survives
            else []
        ),
        root_causes=root_causes,
        node_risk_ranking=calculate_node_risk_ranking(graph),
    )


def predict_failure_impact(
    workflow: WorkflowGraph,
    deployment: DeploymentGraph,
    failed_node: str,
) -> FailureImpactReport:
    """Predict impact after an assumed single-worker-node failure."""

    graph = build_cross_layer_graph(workflow, deployment)
    return _predict_from_graph(graph, failed_node)


def predict_demo_failure_impact(failed_node: str) -> FailureImpactReport:
    """Run the real predictor over disclosed Component 3 contract fixtures."""

    workflow, deployment = get_demo_inputs()
    return predict_failure_impact(workflow, deployment, failed_node)


def predict_runtime_failure_impact(
    workflow_id: str,
    failed_node: str,
    namespace: str = "default",
) -> FailureImpactReport:
    """Predict from Component 1's frozen graph and Component 2's live topology."""

    inputs = get_runtime_component_inputs(workflow_id, namespace)
    return predict_failure_impact(
        inputs.workflow,
        inputs.deployment,
        failed_node,
    )


def get_sample_failure_impact() -> FailureImpactReport:
    """Keep the original sample endpoint while returning a calculated report."""

    return predict_demo_failure_impact("node-1")


__all__ = [
    "build_current_cross_layer_graph",
    "get_demo_deployment",
    "get_sample_failure_impact",
    "predict_demo_failure_impact",
    "predict_failure_impact",
    "predict_portfolio_failure_impact",
    "predict_runtime_failure_impact",
    "RuntimeInputUnavailableError",
]
