"""Integration tests for service.py — Member 1: Workflow Dependency Modeling.

These tests exercise the complete five-stage pipeline through the public
service interface, verifying that:

  - build_workflow_graph() runs end-to-end without errors.
  - get_sample_workflow() produces a correct WorkflowGraph.
  - The /api/workflows/sample endpoint still returns HTTP 200 with valid JSON.
  - The response JSON contains the correct workflow fields.
  - mandatory_threshold and min_edge_frequency parameters propagate correctly.

Integration contract (what Member 3 will receive from Component 1):
  workflow_id:          "checkout"
  criticality:          0.9
  mandatory_services:   [cartservice, checkoutservice, frontend, paymentservice]
  optional_services:    [emailservice]
  edges:                4 edges (all observed, default min_edge_frequency=0.0)
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.engine.workflow_dependency_modeling.service import (
    build_workflow_graph,
    get_sample_workflow,
)
from app.main import app
from app.shared.sample_loader import SAMPLE_DATA_DIR
from app.shared.schemas.workflow import WorkflowGraph
from app.engine.workflow_dependency_modeling.trace_reader import (
    load_traces_from_file,
)

_DEFINITIONS_DIR = SAMPLE_DATA_DIR / "workflow_definitions"
_TRACES_DIR = SAMPLE_DATA_DIR / "traces"

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_def(name: str) -> dict:
    with open(_DEFINITIONS_DIR / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


def load_traces(name: str) -> list[dict]:
    return load_traces_from_file(_TRACES_DIR / f"{name}_traces.json")


# ---------------------------------------------------------------------------
# build_workflow_graph — checkout
# ---------------------------------------------------------------------------


class TestBuildWorkflowGraphCheckout:
    @pytest.fixture(scope="class")
    def graph(self) -> WorkflowGraph:
        definition = load_def("checkout")
        traces = load_traces("checkout")
        return build_workflow_graph(definition, traces)

    def test_returns_workflow_graph(self, graph: WorkflowGraph) -> None:
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

    def test_edge_count(self, graph: WorkflowGraph) -> None:
        assert len(graph.edges) == 4

    def test_camel_case_serialisation(self, graph: WorkflowGraph) -> None:
        data = graph.model_dump(by_alias=True)
        assert "workflowId" in data
        assert "mandatoryServices" in data
        assert "optionalServices" in data
        assert "edges" in data


# ---------------------------------------------------------------------------
# build_workflow_graph — refund
# ---------------------------------------------------------------------------


class TestBuildWorkflowGraphRefund:
    @pytest.fixture(scope="class")
    def graph(self) -> WorkflowGraph:
        definition = load_def("refund")
        traces = load_traces("refund")
        return build_workflow_graph(definition, traces)

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

    def test_edge_count(self, graph: WorkflowGraph) -> None:
        assert len(graph.edges) == 3


# ---------------------------------------------------------------------------
# build_workflow_graph — parameter propagation
# ---------------------------------------------------------------------------


class TestParameterPropagation:
    def test_mandatory_threshold_0_6_promotes_email_to_mandatory(self) -> None:
        definition = load_def("checkout")
        traces = load_traces("checkout")
        graph = build_workflow_graph(
            definition, traces, mandatory_threshold=0.6
        )
        assert "emailservice" in graph.mandatory_services
        assert "emailservice" not in graph.optional_services

    def test_min_edge_frequency_1_0_excludes_email_edge(self) -> None:
        definition = load_def("checkout")
        traces = load_traces("checkout")
        graph = build_workflow_graph(
            definition, traces, min_edge_frequency=1.0
        )
        pairs = {(e.source, e.target) for e in graph.edges}
        assert ("checkoutservice", "emailservice") not in pairs
        assert len(graph.edges) == 3

    def test_min_edge_frequency_0_includes_all_edges(self) -> None:
        definition = load_def("checkout")
        traces = load_traces("checkout")
        graph = build_workflow_graph(definition, traces, min_edge_frequency=0.0)
        assert len(graph.edges) == 4


# ---------------------------------------------------------------------------
# get_sample_workflow
# ---------------------------------------------------------------------------


class TestGetSampleWorkflow:
    @pytest.fixture(scope="class")
    def graph(self) -> WorkflowGraph:
        return get_sample_workflow()

    def test_returns_workflow_graph(self, graph: WorkflowGraph) -> None:
        assert isinstance(graph, WorkflowGraph)

    def test_workflow_id_is_checkout(self, graph: WorkflowGraph) -> None:
        assert graph.workflow_id == "checkout"

    def test_mandatory_services_correct(self, graph: WorkflowGraph) -> None:
        assert sorted(graph.mandatory_services) == [
            "cartservice",
            "checkoutservice",
            "frontend",
            "paymentservice",
        ]

    def test_optional_services_correct(self, graph: WorkflowGraph) -> None:
        assert graph.optional_services == ["emailservice"]

    def test_has_edges(self, graph: WorkflowGraph) -> None:
        assert len(graph.edges) > 0


# ---------------------------------------------------------------------------
# API endpoint integration
# ---------------------------------------------------------------------------


class TestApiEndpoint:
    def test_sample_endpoint_returns_200(self) -> None:
        response = client.get("/api/workflows/sample")
        assert response.status_code == 200

    def test_response_is_json(self) -> None:
        response = client.get("/api/workflows/sample")
        data = response.json()
        assert isinstance(data, dict)

    def test_response_has_workflow_id(self) -> None:
        response = client.get("/api/workflows/sample")
        data = response.json()
        assert data.get("workflowId") == "checkout"

    def test_response_has_mandatory_services(self) -> None:
        response = client.get("/api/workflows/sample")
        data = response.json()
        assert "mandatoryServices" in data
        assert "paymentservice" in data["mandatoryServices"]

    def test_response_has_optional_services(self) -> None:
        response = client.get("/api/workflows/sample")
        data = response.json()
        assert "optionalServices" in data
        assert "emailservice" in data["optionalServices"]

    def test_response_has_edges(self) -> None:
        response = client.get("/api/workflows/sample")
        data = response.json()
        assert "edges" in data
        assert len(data["edges"]) > 0

    def test_response_criticality(self) -> None:
        response = client.get("/api/workflows/sample")
        data = response.json()
        assert data.get("criticality") == pytest.approx(0.9)
