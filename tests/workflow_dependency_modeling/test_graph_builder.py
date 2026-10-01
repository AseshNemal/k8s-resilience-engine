"""Legacy shared-graph adapter tests for internal candidate results."""

import pytest

from app.engine.workflow_dependency_modeling.dependency_classifier import (
    DependencyClassification,
)
from app.engine.workflow_dependency_modeling.graph_builder import build_workflow_graph
from app.engine.workflow_dependency_modeling.interaction_extractor import ObservedEdge
from app.engine.workflow_dependency_modeling.models import SuccessCondition, WorkflowScenario
from app.shared.schemas.workflow import WorkflowGraph


SUCCESS_CONDITION = SuccessCondition.from_mapping(
    {
        "protocol": "http",
        "terminalService": "checkout",
        "operationName": "POST /checkout",
        "httpStatusCodes": [200],
    }
)
SCENARIO = WorkflowScenario(
    workflow_id="checkout",
    description="Synthetic checkout fixture",
    criticality=0.9,
    success_condition=SUCCESS_CONDITION,
    entry_service="frontend",
)
CLASSIFICATION = DependencyClassification(
    workflow_id="checkout",
    mandatory_candidates=["checkout", "frontend"],
    optional_candidates=["email"],
)
EDGES = [
    ObservedEdge("frontend", "checkout", 2, 1.0),
    ObservedEdge("checkout", "email", 1, 0.5),
]


def test_adapter_preserves_shared_contract_and_deterministic_edges() -> None:
    graph = build_workflow_graph(CLASSIFICATION, list(reversed(EDGES)), SCENARIO)
    assert isinstance(graph, WorkflowGraph)
    assert graph.workflow_id == "checkout"
    assert graph.criticality == pytest.approx(0.9)
    assert graph.mandatory_services == ["checkout", "frontend"]
    assert graph.optional_services == ["email"]
    assert [(edge.source, edge.target) for edge in graph.edges] == [
        ("checkout", "email"),
        ("frontend", "checkout"),
    ]


def test_edge_threshold_filters_low_frequency_observation() -> None:
    graph = build_workflow_graph(
        CLASSIFICATION, EDGES, SCENARIO, min_edge_frequency=1.0
    )
    assert [(edge.source, edge.target) for edge in graph.edges] == [
        ("frontend", "checkout")
    ]


def test_mismatched_scenario_is_rejected() -> None:
    other = WorkflowScenario("other", "Other", 0.5, SUCCESS_CONDITION, "frontend")
    with pytest.raises(ValueError, match="does not match"):
        build_workflow_graph(CLASSIFICATION, EDGES, other)


@pytest.mark.parametrize("frequency", [-0.1, 1.1])
def test_invalid_edge_threshold_is_rejected(frequency: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        build_workflow_graph(CLASSIFICATION, EDGES, SCENARIO, frequency)
