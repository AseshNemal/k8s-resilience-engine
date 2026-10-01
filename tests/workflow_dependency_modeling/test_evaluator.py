"""Independent reference evaluation tests."""

import pytest

from app.engine.workflow_dependency_modeling.evaluator import evaluate_graph
from app.engine.workflow_dependency_modeling.models import ReferenceWorkflow
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph


def reference(
    workflow_id: str = "checkout",
    services: list[str] | None = None,
    edges: list[dict[str, str]] | None = None,
) -> ReferenceWorkflow:
    return ReferenceWorkflow.from_mapping(
        {
            "workflowId": workflow_id,
            "expectedServices": services or ["frontend", "checkout"],
            "expectedEdges": edges
            or [{"source": "frontend", "target": "checkout"}],
            "synthetic": True,
        }
    )


def graph() -> WorkflowGraph:
    return WorkflowGraph(
        workflow_id="checkout",
        criticality=0.9,
        mandatory_services=["frontend", "checkout"],
        optional_services=[],
        edges=[WorkflowEdge(source="frontend", target="checkout")],
    )


def test_perfect_reference_match() -> None:
    result = evaluate_graph(graph(), reference())
    assert result.service_metrics.precision == pytest.approx(1.0)
    assert result.service_metrics.recall == pytest.approx(1.0)
    assert result.service_metrics.f1 == pytest.approx(1.0)
    assert result.edge_metrics.accuracy == pytest.approx(1.0)


def test_false_positive_and_false_negative_details() -> None:
    result = evaluate_graph(
        graph(),
        reference(
            services=["frontend", "payment"],
            edges=[{"source": "frontend", "target": "payment"}],
        ),
    )
    assert result.false_positive_services == ["checkout"]
    assert result.false_negative_services == ["payment"]
    assert result.false_positive_edges == [("frontend", "checkout")]
    assert result.false_negative_edges == [("frontend", "payment")]
    assert result.service_metrics.precision == pytest.approx(0.5)
    assert result.service_metrics.recall == pytest.approx(0.5)


def test_evaluator_rejects_reference_for_another_workflow() -> None:
    with pytest.raises(ValueError, match="IDs do not match"):
        evaluate_graph(graph(), reference(workflow_id="refund"))
