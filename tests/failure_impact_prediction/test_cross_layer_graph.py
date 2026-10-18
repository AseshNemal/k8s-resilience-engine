import pytest

from app.engine.failure_impact_prediction.cross_layer_graph import (
    CrossLayerGraphError,
    IncompleteTopologyError,
    build_cross_layer_graph,
)
from app.engine.failure_impact_prediction.fixture_provider import (
    get_current_component_inputs,
    get_demo_inputs,
)


def test_current_component_outputs_join_and_report_unresolved_services() -> None:
    workflow, deployment = get_current_component_inputs()

    graph = build_cross_layer_graph(
        workflow,
        deployment,
        require_complete=False,
    )

    assert "paymentservice" not in graph.unresolved_services
    assert "cartservice" in graph.unresolved_services
    assert "checkoutservice" in graph.unresolved_services
    assert graph.service("paymentservice") is not None


def test_incomplete_current_topology_is_rejected_for_prediction() -> None:
    workflow, deployment = get_current_component_inputs()

    with pytest.raises(IncompleteTopologyError, match="cartservice"):
        build_cross_layer_graph(workflow, deployment)


def test_demo_graph_has_all_cross_layer_relationships() -> None:
    workflow, deployment = get_demo_inputs()

    graph = build_cross_layer_graph(workflow, deployment)
    relationships = {edge.relationship for edge in graph.edges}

    assert not set(graph.mandatory_services).intersection(
        graph.unresolved_services
    )
    assert {
        "REQUIRES_MANDATORY",
        "USES_OPTIONAL",
        "CALLS",
        "HAS_REPLICA",
        "RUNS_ON",
        "IN_FAILURE_DOMAIN",
    }.issubset(relationships)
    assert {node.node_type for node in graph.nodes} == {
        "WORKFLOW",
        "SERVICE",
        "POD",
        "NODE",
        "FAILURE_DOMAIN",
    }


def test_duplicate_workflow_service_classification_is_rejected() -> None:
    workflow, deployment = get_demo_inputs()
    workflow.mandatory_services.append(workflow.mandatory_services[0])

    with pytest.raises(
        CrossLayerGraphError,
        match="Duplicate mandatory workflow services",
    ):
        build_cross_layer_graph(workflow, deployment)


def test_conflicting_workflow_service_classification_is_rejected() -> None:
    workflow, deployment = get_demo_inputs()
    workflow.optional_services.append(workflow.mandatory_services[0])

    with pytest.raises(
        CrossLayerGraphError,
        match="both mandatory and optional",
    ):
        build_cross_layer_graph(workflow, deployment)


def test_duplicate_workflow_edges_are_rejected() -> None:
    workflow, deployment = get_demo_inputs()
    workflow.edges.append(workflow.edges[0].model_copy(deep=True))

    with pytest.raises(CrossLayerGraphError, match="Duplicate workflow edges"):
        build_cross_layer_graph(workflow, deployment)


def test_duplicate_deployment_node_names_are_rejected() -> None:
    workflow, deployment = get_demo_inputs()
    deployment.nodes.append(deployment.nodes[0].model_copy(deep=True))

    with pytest.raises(CrossLayerGraphError, match="Duplicate deployment node names"):
        build_cross_layer_graph(workflow, deployment)


def test_duplicate_deployment_service_names_are_rejected() -> None:
    workflow, deployment = get_demo_inputs()
    deployment.services.append(deployment.services[0].model_copy(deep=True))

    with pytest.raises(
        CrossLayerGraphError,
        match="Duplicate deployment service names",
    ):
        build_cross_layer_graph(workflow, deployment)


def test_duplicate_pod_names_are_rejected() -> None:
    workflow, deployment = get_demo_inputs()
    duplicate_pod = deployment.services[0].pods[0].model_copy(deep=True)
    deployment.services[1].pods.append(duplicate_pod)

    with pytest.raises(CrossLayerGraphError, match="Duplicate deployment pod names"):
        build_cross_layer_graph(workflow, deployment)


def test_pods_assigned_to_unknown_nodes_are_rejected() -> None:
    workflow, deployment = get_demo_inputs()
    deployment.services[0].pods[0].node = "node-99"

    with pytest.raises(
        CrossLayerGraphError,
        match="Pods reference unknown deployment nodes: node-99",
    ):
        build_cross_layer_graph(workflow, deployment)
