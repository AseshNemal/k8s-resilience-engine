"""Tests for evaluator.py — Member 1: Workflow Dependency Modeling.

Verifies precision, recall, F1, and Jaccard accuracy at both edge and
service level across several scenarios:

Scenario A — perfect match (default min_edge_frequency=0.0):
  All 4 checkout edges match the reference → precision=recall=F1=accuracy=1.0

Scenario B — mandatory-only graph (min_edge_frequency=1.0):
  checkout→emailservice edge is excluded.
  - TP=3, FP=0, FN=1
  - edge precision = 3/(3+0) = 1.0
  - edge recall    = 3/(3+1) = 0.75
  - edge F1        = 2*(1.0*0.75)/(1.0+0.75) ≈ 0.857
  - edge accuracy  = 3/(3+0+1) = 0.75

Scenario C — refund perfect match (default):
  All 3 edges match the 3 reference edges → 1.0 on all metrics.

Scenario D — false-positive edge (synthetic):
  A graph edge not in the reference → FP detected.

Scenario E — false-negative edge (synthetic):
  A reference edge missing from the graph → FN detected.
"""

import json
from pathlib import Path

import pytest

from app.engine.workflow_dependency_modeling.dependency_classifier import (
    classify_dependencies,
)
from app.engine.workflow_dependency_modeling.evaluator import (
    EvaluationMetrics,
    GraphEvaluationResult,
    evaluate_graph,
)
from app.engine.workflow_dependency_modeling.graph_builder import (
    build_workflow_graph,
)
from app.engine.workflow_dependency_modeling.interaction_extractor import (
    aggregate_edges,
    extract_interactions,
)
from app.engine.workflow_dependency_modeling.trace_reader import (
    load_traces_from_file,
    parse_spans,
)
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph

TRACES_DIR = Path("sample_data/traces")
DEFINITIONS_DIR = Path("sample_data/workflow_definitions")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_def(name: str) -> dict:
    with open(DEFINITIONS_DIR / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


def make_graph(workflow: str, min_edge_frequency: float = 0.0) -> WorkflowGraph:
    raw = load_traces_from_file(TRACES_DIR / f"{workflow}_traces.json")
    spans = parse_spans(raw)
    interactions = extract_interactions(spans)
    observed = aggregate_edges(interactions)
    definition = load_def(workflow)
    classification = classify_dependencies(interactions, definition)
    return build_workflow_graph(
        classification, observed, definition, min_edge_frequency=min_edge_frequency
    )


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class TestDataTypes:
    def test_evaluation_metrics_is_dataclass(self) -> None:
        m = EvaluationMetrics(
            precision=1.0,
            recall=1.0,
            f1=1.0,
            accuracy=1.0,
            true_positives=4,
            false_positives=0,
            false_negatives=0,
        )
        assert m.precision == 1.0

    def test_graph_evaluation_result_is_dataclass(self) -> None:
        result = evaluate_graph(make_graph("checkout"), load_def("checkout"))
        assert isinstance(result, GraphEvaluationResult)
        assert isinstance(result.edge_metrics, EvaluationMetrics)
        assert isinstance(result.service_metrics, EvaluationMetrics)


# ---------------------------------------------------------------------------
# Scenario A — checkout, default parameters (perfect match expected)
# ---------------------------------------------------------------------------


class TestCheckoutPerfectMatch:
    @pytest.fixture(scope="class")
    def result(self) -> GraphEvaluationResult:
        graph = make_graph("checkout")
        return evaluate_graph(graph, load_def("checkout"))

    def test_workflow_id(self, result: GraphEvaluationResult) -> None:
        assert result.workflow_id == "checkout"

    # -- Edge metrics -------------------------------------------------------

    def test_edge_precision_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.precision == pytest.approx(1.0)

    def test_edge_recall_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.recall == pytest.approx(1.0)

    def test_edge_f1_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.f1 == pytest.approx(1.0)

    def test_edge_accuracy_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.accuracy == pytest.approx(1.0)

    def test_edge_true_positives(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.true_positives == 4

    def test_edge_no_false_positives(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.false_positives == 0
        assert result.false_positive_edges == []

    def test_edge_no_false_negatives(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.false_negatives == 0
        assert result.false_negative_edges == []

    # -- Service metrics ----------------------------------------------------

    def test_service_precision_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.service_metrics.precision == pytest.approx(1.0)

    def test_service_recall_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.service_metrics.recall == pytest.approx(1.0)

    def test_service_f1_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.service_metrics.f1 == pytest.approx(1.0)

    def test_service_true_positives(self, result: GraphEvaluationResult) -> None:
        # 5 services: frontend, cartservice, checkoutservice,
        #             paymentservice, emailservice
        assert result.service_metrics.true_positives == 5

    def test_service_no_false_positives(
        self, result: GraphEvaluationResult
    ) -> None:
        assert result.false_positive_services == []

    def test_service_no_false_negatives(
        self, result: GraphEvaluationResult
    ) -> None:
        assert result.false_negative_services == []

    # -- TP edges list ------------------------------------------------------

    def test_tp_edges_contains_all_four(
        self, result: GraphEvaluationResult
    ) -> None:
        tp_pairs = set(result.true_positive_edges)
        assert ("cartservice", "checkoutservice") in tp_pairs
        assert ("checkoutservice", "emailservice") in tp_pairs
        assert ("checkoutservice", "paymentservice") in tp_pairs
        assert ("frontend", "cartservice") in tp_pairs


# ---------------------------------------------------------------------------
# Scenario B — checkout, mandatory-edges-only (min_edge_frequency=1.0)
# ---------------------------------------------------------------------------


class TestCheckoutMandatoryEdgesOnly:
    @pytest.fixture(scope="class")
    def result(self) -> GraphEvaluationResult:
        graph = make_graph("checkout", min_edge_frequency=1.0)
        return evaluate_graph(graph, load_def("checkout"))

    def test_edge_precision_is_one(self, result: GraphEvaluationResult) -> None:
        # No wrong edges predicted
        assert result.edge_metrics.precision == pytest.approx(1.0)

    def test_edge_recall_is_0_75(self, result: GraphEvaluationResult) -> None:
        # 3 of 4 reference edges recovered
        assert result.edge_metrics.recall == pytest.approx(0.75)

    def test_edge_f1_approx_0_857(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.f1 == pytest.approx(
            2 * 1.0 * 0.75 / (1.0 + 0.75), abs=0.01
        )

    def test_edge_accuracy_is_0_75(self, result: GraphEvaluationResult) -> None:
        # Jaccard: 3 / (3 + 0 + 1) = 0.75
        assert result.edge_metrics.accuracy == pytest.approx(0.75)

    def test_false_negative_is_email_edge(
        self, result: GraphEvaluationResult
    ) -> None:
        assert ("checkoutservice", "emailservice") in result.false_negative_edges

    def test_no_false_positives(self, result: GraphEvaluationResult) -> None:
        assert result.false_positive_edges == []

    def test_true_positives_count(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.true_positives == 3

    def test_false_negatives_count(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.false_negatives == 1


# ---------------------------------------------------------------------------
# Scenario C — refund, perfect match (default)
# ---------------------------------------------------------------------------


class TestRefundPerfectMatch:
    @pytest.fixture(scope="class")
    def result(self) -> GraphEvaluationResult:
        graph = make_graph("refund")
        return evaluate_graph(graph, load_def("refund"))

    def test_workflow_id(self, result: GraphEvaluationResult) -> None:
        assert result.workflow_id == "refund"

    def test_edge_precision_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.precision == pytest.approx(1.0)

    def test_edge_recall_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.recall == pytest.approx(1.0)

    def test_edge_f1_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.f1 == pytest.approx(1.0)

    def test_service_f1_is_one(self, result: GraphEvaluationResult) -> None:
        assert result.service_metrics.f1 == pytest.approx(1.0)

    def test_three_tp_edges(self, result: GraphEvaluationResult) -> None:
        assert result.edge_metrics.true_positives == 3


# ---------------------------------------------------------------------------
# Scenario D — synthetic false-positive edge
# ---------------------------------------------------------------------------


class TestFalsePositiveEdge:
    def test_unexpected_edge_is_detected(self) -> None:
        """A graph edge not present in the reference must appear in FP list."""
        graph = WorkflowGraph(
            workflow_id="synthetic",
            criticality=0.8,
            mandatory_services=["frontend", "api"],
            optional_services=[],
            edges=[
                WorkflowEdge(source="frontend", target="api"),
                WorkflowEdge(source="frontend", target="unexpected"),  # FP
            ],
        )
        definition = {
            "workflowId": "synthetic",
            "expectedEdges": [{"source": "frontend", "target": "api"}],
            "expectedServices": ["frontend", "api"],
        }
        result = evaluate_graph(graph, definition)

        assert result.edge_metrics.false_positives == 1
        assert ("frontend", "unexpected") in result.false_positive_edges
        assert result.edge_metrics.precision == pytest.approx(0.5)
        assert result.edge_metrics.recall == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Scenario E — synthetic false-negative edge
# ---------------------------------------------------------------------------


class TestFalseNegativeEdge:
    def test_missing_reference_edge_is_detected(self) -> None:
        """A reference edge absent from the graph must appear in FN list."""
        graph = WorkflowGraph(
            workflow_id="synthetic",
            criticality=0.8,
            mandatory_services=["frontend", "api"],
            optional_services=[],
            edges=[
                WorkflowEdge(source="frontend", target="api"),
            ],
        )
        definition = {
            "workflowId": "synthetic",
            "expectedEdges": [
                {"source": "frontend", "target": "api"},
                {"source": "api", "target": "db"},  # FN — not in generated graph
            ],
            "expectedServices": ["frontend", "api"],
        }
        result = evaluate_graph(graph, definition)

        assert result.edge_metrics.false_negatives == 1
        assert ("api", "db") in result.false_negative_edges
        assert result.edge_metrics.precision == pytest.approx(1.0)
        assert result.edge_metrics.recall == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_graph_against_empty_reference(self) -> None:
        graph = WorkflowGraph(
            workflow_id="empty",
            criticality=0.5,
            mandatory_services=[],
            optional_services=[],
            edges=[],
        )
        definition = {
            "workflowId": "empty",
            "expectedEdges": [],
            "expectedServices": [],
        }
        result = evaluate_graph(graph, definition)
        # All zeros — no TP, FP, FN
        assert result.edge_metrics.precision == pytest.approx(0.0)
        assert result.edge_metrics.recall == pytest.approx(0.0)
        assert result.edge_metrics.accuracy == pytest.approx(0.0)

    def test_missing_expected_edges_key_treated_as_empty(self) -> None:
        graph = WorkflowGraph(
            workflow_id="wf",
            criticality=0.5,
            mandatory_services=["frontend"],
            optional_services=[],
            edges=[WorkflowEdge(source="frontend", target="api")],
        )
        result = evaluate_graph(graph, {"workflowId": "wf"})
        # Reference is empty → all predicted edges are FP
        assert result.edge_metrics.false_positives == 1
        assert result.edge_metrics.true_positives == 0
