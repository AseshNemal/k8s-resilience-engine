"""Public service interface for Component 3 failure-impact prediction."""

from app.engine.failure_impact_prediction.blast_radius import (
    calculate_blast_radius,
    calculate_node_risk_ranking,
)
from app.engine.failure_impact_prediction.cross_layer_graph import (
    build_cross_layer_graph,
)
from app.engine.failure_impact_prediction.deployment_impact import (
    predict_deployment_failure_impact,
)
from app.engine.failure_impact_prediction.fixture_provider import (
    get_current_component_inputs,
    get_demo_deployment,
    get_demo_inputs,
    get_demo_portfolio_inputs,
)
from app.engine.failure_impact_prediction.models import (
    CrossLayerGraph,
    PortfolioFailureImpact,
)
from app.engine.failure_impact_prediction.node_failure_simulator import (
    simulate_node_failure,
)
from app.engine.failure_impact_prediction.overview import build_failure_overview
from app.engine.failure_impact_prediction.portfolio_impact import (
    predict_portfolio_failure_impact,
)
from app.engine.failure_impact_prediction.runtime_provider import (
    RuntimeComponentInputs,
    RuntimeInputUnavailableError,
    get_runtime_component_inputs,
    get_runtime_portfolio_inputs,
)
from app.engine.failure_impact_prediction.service_survival import (
    calculate_service_survival,
)
from app.engine.failure_impact_prediction.workflow_survival import (
    calculate_workflow_survival,
)
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import (
    FailureImpactReport,
    PortfolioFailureImpactReport,
    PortfolioNodeRisk,
    PortfolioWorkflowImpact,
    RootCause,
)
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


def predict_runtime_deployment_failure_impact_with_evidence(
    failed_node: str,
    namespace: str = "default",
):
    """Predict deployment-level service exposure without requiring workflow evidence."""
    from app.engine.deployment_topology_modeling.service import discover_live

    discovery = discover_live(namespace=namespace)
    if discovery.status == "partial" or any(not evidence.complete for evidence in discovery.service_evidence.values()):
        from app.shared.evidence_errors import EvidenceError
        raise EvidenceError("TOPOLOGY_INCOMPLETE", "Deployment placement is incomplete, so service survival cannot be predicted reliably.",
                            http_status=409, component="failure_impact")
    report = predict_deployment_failure_impact(discovery.graph, failed_node)
    return report, discovery


class ApplicationSnapshot:
    """One cluster read: the public failure overview, the namespace discovery it used, and the workflows that were scored."""

    def __init__(self, public: dict, discovery=None, evaluated_workflows: tuple = ()):
        self.public = public
        self.discovery = discovery
        self.evaluated_workflows = evaluated_workflows


def load_application_snapshot(
    namespace: str | None = None,
    workflow_ids: tuple[str, ...] = (),
) -> ApplicationSnapshot:
    """Read the cluster once and assess every worker of the chosen application on that snapshot.

    The application is the requested namespace, or the only namespace that holds applications. With several and
    none requested, the answer lists them and asks for a choice (it never picks one). Workflows come from
    ``workflow_basis`` (current operational reviews only; never invented).
    """
    from app.engine.deployment_topology_modeling.discovery_all import discover_all
    from app.engine.failure_impact_prediction import workflow_basis

    overview = discover_all(include_system=False)
    applications = [result for result in overview.namespaces if result.graph.services]
    names = [result.namespace for result in applications]
    requested = (namespace or "").strip() or None

    base = {
        "readOnly": True,
        "isPrediction": True,
        "capturedAt": overview.captured_at,
        "cluster": {"fingerprint": overview.cluster.connection_fingerprint},
        "namespaces": names,
        "applications": [
            {"namespace": item.namespace, "services": sorted(service.name for service in item.graph.services),
             "workerNodes": sorted(node.name for node in item.graph.nodes)}
            for item in applications
        ],
    }
    if requested and requested not in names:
        raise EvidenceError(
            "APPLICATION_NOT_FOUND",
            f"The namespace {requested} holds no application in the connected cluster.",
            http_status=404, component="failure_impact",
        )
    if not requested and len(names) != 1:
        return ApplicationSnapshot({**base, "state": "choose-application" if names else "no-application", "namespace": None})
    if not requested:
        requested = names[0]

    result = next(item for item in applications if item.namespace == requested)
    incomplete = sorted(name for name, evidence in result.service_evidence.items() if not evidence.complete)
    candidates, problems = workflow_basis.resolve_workflows(result, requested, workflow_ids)
    included = [candidate.basis for candidate in candidates if candidate.included]
    body = build_failure_overview(result.graph, included)
    evaluated_ids = {row["workflowId"] for row in body["workflowsEvaluated"]}
    public = {
        **base,
        "state": "ready",
        "namespace": requested,
        "namespaceChoice": "requested" if namespace else "only-application",
        "topology": {
            "status": result.status,
            "complete": not incomplete and result.status != "partial",
            "incompleteServices": incomplete,
            "warnings": sorted({warning.code for warning in result.warnings}),
        },
        "basis": {**workflow_basis.basis_label(candidates), "workflows": workflow_basis.describe_candidates(candidates),
                  "problems": problems},
        **body,
    }
    evaluated = tuple(basis.workflow for basis in included if basis.workflow.workflow_id in evaluated_ids)
    return ApplicationSnapshot(public, result, evaluated)


def build_cluster_failure_overview(
    namespace: str | None = None,
    workflow_ids: tuple[str, ...] = (),
) -> dict:
    """Every worker node of the connected cluster's application assessed as the failed node, from one read."""
    return load_application_snapshot(namespace, workflow_ids).public


def predict_demo_failure_impact(failed_node: str) -> FailureImpactReport:
    """Run the real predictor over disclosed Component 3 contract fixtures."""

    workflow, deployment = get_demo_inputs()
    return predict_failure_impact(workflow, deployment, failed_node)


def predict_runtime_failure_impact_with_evidence(
    workflow_id: str,
    failed_node: str,
    namespace: str = "default",
) -> tuple[FailureImpactReport, RuntimeComponentInputs]:
    """Predict from Component 1's frozen graph and Component 2's live topology.

    Returns the report together with the evidence it was computed from (topology status, capture
    time, warnings and unresolved items), so callers can show how complete the input was. The report
    is a prediction about a virtual node failure, not an observation.
    """

    inputs = get_runtime_component_inputs(workflow_id, namespace)
    report = predict_failure_impact(inputs.workflow, inputs.deployment, failed_node)
    return report, inputs


def predict_demo_portfolio_failure_impact(
    failed_node: str,
) -> PortfolioFailureImpact:
    """Run the portfolio evaluator over disclosed contract fixtures."""

    workflows, deployment = get_demo_portfolio_inputs()
    return predict_portfolio_failure_impact(
        workflows,
        deployment,
        failed_node,
    )


def predict_runtime_failure_impact(
    workflow_id: str,
    failed_node: str,
    namespace: str = "default",
) -> FailureImpactReport:
    """Predict from Component 1's frozen graph and Component 2's live topology."""

    return predict_runtime_failure_impact_with_evidence(workflow_id, failed_node, namespace)[0]


def predict_runtime_portfolio_failure_impact(
    workflow_ids: tuple[str, ...],
    failed_node: str,
    namespace: str = "default",
) -> PortfolioFailureImpact:
    """Predict portfolio impact from frozen workflows and one live topology."""

    inputs = get_runtime_portfolio_inputs(workflow_ids, namespace)
    return predict_portfolio_failure_impact(
        inputs.workflows,
        inputs.deployment,
        failed_node,
    )


def build_portfolio_failure_impact_report(
    prediction: PortfolioFailureImpact,
) -> PortfolioFailureImpactReport:
    """Convert the internal immutable result to the public API contract."""

    return PortfolioFailureImpactReport(
        failed_node=prediction.failed_node,
        total_workflows=prediction.total_workflows,
        failed_workflows=list(prediction.failed_workflows),
        surviving_workflows=list(prediction.surviving_workflows),
        failed_services=list(prediction.failed_services),
        blast_radius=prediction.blast_radius,
        survivability=prediction.survivability,
        workflow_impacts=[
            PortfolioWorkflowImpact(
                workflow_id=impact.workflow_id,
                survives=impact.survives,
                failed_services=list(impact.failed_services),
                failed_mandatory_services=list(
                    impact.failed_mandatory_services
                ),
            )
            for impact in prediction.workflow_impacts
        ],
        node_risk_ranking=[
            PortfolioNodeRisk(
                node=risk.node,
                blast_radius=risk.blast_radius,
                failed_workflows=list(risk.failed_workflows),
            )
            for risk in prediction.node_risk_ranking
        ],
    )


def get_sample_failure_impact() -> FailureImpactReport:
    """Test-fixture helper: a calculated report over the contract fixtures. No route or page uses it."""

    return predict_demo_failure_impact("node-1")


__all__ = [
    "ApplicationSnapshot",
    "build_cluster_failure_overview",
    "load_application_snapshot",
    "build_portfolio_failure_impact_report",
    "build_current_cross_layer_graph",
    "get_demo_deployment",
    "get_sample_failure_impact",
    "predict_demo_failure_impact",
    "predict_demo_portfolio_failure_impact",
    "predict_failure_impact",
    "predict_portfolio_failure_impact",
    "predict_runtime_failure_impact_with_evidence",
    "predict_runtime_failure_impact",
    "predict_runtime_portfolio_failure_impact",
    "RuntimeInputUnavailableError",
]
