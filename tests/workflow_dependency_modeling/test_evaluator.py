"""Independent reference evaluation tests."""

import pytest

from app.engine.workflow_dependency_modeling.evaluator import evaluate_graph
from app.engine.workflow_dependency_modeling.models import ReferenceWorkflow
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph


def reference(
    workflow_id: str = "checkout",
    services: list[str] | None = None,
    mandatory_services: list[str] | None = None,
    optional_services: list[str] | None = None,
    edges: list[dict[str, str]] | None = None,
    mandatory_edges: list[dict[str, str]] | None = None,
    optional_edges: list[dict[str, str]] | None = None,
) -> ReferenceWorkflow:
    expected_services = services or ["frontend", "checkout"]
    expected_mandatory = mandatory_services
    if expected_mandatory is None:
        expected_mandatory = expected_services
    expected_optional = optional_services or []
    expected_edges = edges or [{"source": "frontend", "target": "checkout"}]
    expected_mandatory_edges = mandatory_edges
    if expected_mandatory_edges is None:
        expected_mandatory_edges = expected_edges
    return ReferenceWorkflow.from_mapping(
        {
            "workflowId": workflow_id,
            "expectedServices": expected_services,
            "mandatoryServices": expected_mandatory,
            "optionalServices": expected_optional,
            "expectedEdges": expected_edges,
            "mandatoryEdges": expected_mandatory_edges,
            "optionalEdges": optional_edges or [],
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
    assert result.mandatory_service_metrics.f1 == pytest.approx(1.0)
    assert result.edge_metrics.accuracy == pytest.approx(1.0)
    assert result.mandatory_edge_metrics.f1 == pytest.approx(1.0)


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


def test_mandatory_service_metrics_detect_optional_misclassification() -> None:
    generated = graph().model_copy(
        update={
            "mandatory_services": [],
            "optional_services": ["frontend", "checkout"],
        }
    )

    result = evaluate_graph(generated, reference())

    assert result.service_metrics.recall == pytest.approx(1.0)
    assert result.mandatory_service_metrics.precision == pytest.approx(0.0)
    assert result.mandatory_service_metrics.recall == pytest.approx(0.0)
    assert result.false_negative_mandatory_services == ["checkout", "frontend"]


def test_mandatory_edge_metrics_exclude_optional_reference_edges() -> None:
    generated = WorkflowGraph(
        workflow_id="checkout",
        criticality=0.9,
        mandatory_services=["frontend", "checkout"],
        optional_services=["email"],
        edges=[
            WorkflowEdge(source="frontend", target="checkout"),
            WorkflowEdge(source="checkout", target="email"),
        ],
    )
    result = evaluate_graph(
        generated,
        reference(
            services=["frontend", "checkout", "email"],
            mandatory_services=["frontend", "checkout"],
            optional_services=["email"],
            edges=[
                {"source": "frontend", "target": "checkout"},
                {"source": "checkout", "target": "email"},
            ],
            mandatory_edges=[{"source": "frontend", "target": "checkout"}],
            optional_edges=[{"source": "checkout", "target": "email"}],
        ),
    )

    assert result.edge_metrics.f1 == pytest.approx(1.0)
    assert result.mandatory_edge_metrics.f1 == pytest.approx(1.0)
    assert result.true_positive_mandatory_edges == [("frontend", "checkout")]


def test_evaluator_rejects_reference_for_another_workflow() -> None:
    with pytest.raises(ValueError, match="IDs do not match"):
        evaluate_graph(graph(), reference(workflow_id="refund"))
