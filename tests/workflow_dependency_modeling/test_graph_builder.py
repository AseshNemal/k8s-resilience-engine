"""Tests for graph_builder.py — Member 1: Workflow Dependency Modeling.

Verifies:
  - build_workflow_graph() produces a valid WorkflowGraph Pydantic model.
  - workflow_id, criticality, mandatory_services, optional_services are correct.
  - All observed edges are included at default min_edge_frequency=0.0.
  - min_edge_frequency=1.0 excludes optional-service edges (emailservice).
  - Edges are WorkflowEdge instances with source/target fields.
  - Both checkout and refund produce correct graphs.
  - The returned object passes WorkflowGraph schema validation (Pydantic).

Expected outputs:
  checkout (min_edge_frequency=0.0):
    workflow_id:          "checkout"
    criticality:          0.9
    mandatory_services:   [cartservice, checkoutservice, frontend, paymentservice]
    optional_services:    [emailservice]
    edges (count):        4

  checkout (min_edge_frequency=1.0):
    edges (count):        3  (checkout→email excluded, freq≈0.667)

  refund (min_edge_frequency=0.0):
    workflow_id:          "refund"
    criticality:          0.7
    mandatory_services:   [frontend, orderservice, paymentservice]
    optional_services:    [notificationservice]
    edges (count):        3

  refund (min_edge_frequency=1.0):
    edges (count):        2  (payment→notification excluded)
"""

import json
from pathlib import Path

import pytest

from app.engine.workflow_dependency_modeling.dependency_classifier import (
    classify_dependencies,
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
# Shared helpers
# ---------------------------------------------------------------------------


def load_def(name: str) -> dict:
    with open(DEFINITIONS_DIR / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


def full_pipeline(workflow: str) -> tuple:
    """Run the full Part 1→4 pipeline for a named workflow.

    Returns (classification, observed_edges, definition).
    """
    raw = load_traces_from_file(TRACES_DIR / f"{workflow}_traces.json")
    spans = parse_spans(raw)
    interactions = extract_interactions(spans)
    observed = aggregate_edges(interactions)
    definition = load_def(workflow)
    classification = classify_dependencies(interactions, definition)
    return classification, observed, definition


# ---------------------------------------------------------------------------
# WorkflowGraph — checkout
# ---------------------------------------------------------------------------


class TestBuildCheckoutGraph:
    @pytest.fixture(scope="class")
    def graph(self) -> WorkflowGraph:
        classification, observed, definition = full_pipeline("checkout")
        return build_workflow_graph(classification, observed, definition)

    def test_returns_workflow_graph_instance(self, graph: WorkflowGraph) -> None:
        assert isinstance(graph, WorkflowGraph)

    def test_workflow_id(self, graph: WorkflowGraph) -> None:
        assert graph.workflow_id == "checkout"

    def test_criticality(self, graph: WorkflowGraph) -> None:
        assert graph.criticality == pytest.approx(0.9)

    def test_mandatory_services(self, graph: WorkflowGraph) -> None:
        assert sorted(graph.mandatory_services) == [
            "cartservice",
            "checkoutservice",
            "frontend",
            "paymentservice",
        ]

    def test_optional_services(self, graph: WorkflowGraph) -> None:
        assert graph.optional_services == ["emailservice"]

    def test_all_four_edges_included_by_default(
        self, graph: WorkflowGraph
    ) -> None:
        assert len(graph.edges) == 4

    def test_edges_are_workflow_edge_instances(
        self, graph: WorkflowGraph
    ) -> None:
        for edge in graph.edges:
            assert isinstance(edge, WorkflowEdge)

    def test_edges_have_source_and_target(self, graph: WorkflowGraph) -> None:
        for edge in graph.edges:
            assert isinstance(edge.source, str)
            assert edge.source
            assert isinstance(edge.target, str)
            assert edge.target

    def test_mandatory_edges_present(self, graph: WorkflowGraph) -> None:
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("frontend", "cartservice") in pairs
        assert ("cartservice", "checkoutservice") in pairs
        assert ("checkoutservice", "paymentservice") in pairs

    def test_optional_edge_present_at_default_threshold(
        self, graph: WorkflowGraph
    ) -> None:
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("checkoutservice", "emailservice") in pairs

    def test_pydantic_serialises_to_camel_case(
        self, graph: WorkflowGraph
    ) -> None:
        data = graph.model_dump(by_alias=True)
        assert "workflowId" in data
        assert "mandatoryServices" in data
        assert "optionalServices" in data


# ---------------------------------------------------------------------------
# WorkflowGraph — checkout with min_edge_frequency=1.0
# ---------------------------------------------------------------------------


class TestBuildCheckoutGraphMandatoryEdgesOnly:
    @pytest.fixture(scope="class")
    def graph(self) -> WorkflowGraph:
        classification, observed, definition = full_pipeline("checkout")
        return build_workflow_graph(
            classification, observed, definition, min_edge_frequency=1.0
        )

    def test_three_edges_mandatory_only(self, graph: WorkflowGraph) -> None:
        assert len(graph.edges) == 3

    def test_email_edge_excluded(self, graph: WorkflowGraph) -> None:
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("checkoutservice", "emailservice") not in pairs

    def test_mandatory_edges_still_present(self, graph: WorkflowGraph) -> None:
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("frontend", "cartservice") in pairs
        assert ("cartservice", "checkoutservice") in pairs
        assert ("checkoutservice", "paymentservice") in pairs


# ---------------------------------------------------------------------------
# WorkflowGraph — refund
# ---------------------------------------------------------------------------


class TestBuildRefundGraph:
    @pytest.fixture(scope="class")
    def graph(self) -> WorkflowGraph:
        classification, observed, definition = full_pipeline("refund")
        return build_workflow_graph(classification, observed, definition)

    def test_workflow_id(self, graph: WorkflowGraph) -> None:
        assert graph.workflow_id == "refund"

    def test_criticality(self, graph: WorkflowGraph) -> None:
        assert graph.criticality == pytest.approx(0.7)

    def test_mandatory_services(self, graph: WorkflowGraph) -> None:
        assert sorted(graph.mandatory_services) == [
            "frontend",
            "orderservice",
            "paymentservice",
        ]

    def test_optional_services(self, graph: WorkflowGraph) -> None:
        assert graph.optional_services == ["notificationservice"]

    def test_all_three_edges_included_by_default(
        self, graph: WorkflowGraph
    ) -> None:
        assert len(graph.edges) == 3

    def test_notification_edge_present(self, graph: WorkflowGraph) -> None:
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("paymentservice", "notificationservice") in pairs

    def test_mandatory_edges_present(self, graph: WorkflowGraph) -> None:
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("frontend", "orderservice") in pairs
        assert ("orderservice", "paymentservice") in pairs


class TestBuildRefundGraphMandatoryEdgesOnly:
    @pytest.fixture(scope="class")
    def graph(self) -> WorkflowGraph:
        classification, observed, definition = full_pipeline("refund")
        return build_workflow_graph(
            classification, observed, definition, min_edge_frequency=1.0
        )

    def test_two_edges_mandatory_only(self, graph: WorkflowGraph) -> None:
        assert len(graph.edges) == 2

    def test_notification_edge_excluded(self, graph: WorkflowGraph) -> None:
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("paymentservice", "notificationservice") not in pairs
