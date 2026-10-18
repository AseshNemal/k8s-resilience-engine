import pytest

from app.engine.failure_impact_prediction.cross_layer_graph import (
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
