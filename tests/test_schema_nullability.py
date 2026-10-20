"""Nullable DeploymentGraph fields: serialization and every downstream consumer must cope with unknowns."""

import json
from pathlib import Path

import pytest

from app.engine.deployment_topology_modeling.discovery import discover
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.service import evaluate_first_recommendation
from app.shared.schemas.deployment import DeploymentGraph, PodModel, ValidationMetrics
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod

SAMPLE_DEPLOYMENT = Path(__file__).resolve().parents[1] / "sample_data" / "deployment.json"


def unknown_everything_cluster() -> FakeClusterSource:
    """Every optional fact is unknown: no zone, no capacity, no restart counts."""
    return FakeClusterSource(
        nodes=[node("w1", None, cpu=None, memory=None), node("w2", None, cpu=None, memory=None)],
        pods={"ns": [
            pod("api-1", "w1", app="api", restarts=None), pod("api-2", "w1", app="api", restarts=None),
            pod("db-1", "w2", app="db", restarts=None),
        ]},
        deployments={"ns": [deployment("api", 2), deployment("db", 1)]},
    )


def workflow() -> WorkflowGraph:
    return WorkflowGraph(workflow_id="wf", criticality=0.9, mandatory_services=["api", "db"],
                         edges=[WorkflowEdge(source="api", target="db")])


# ---- serialization ------------------------------------------------------------------------------------------

def test_unknown_values_serialize_as_null_with_the_existing_camel_case_aliases():
    graph = discover("ns", source=unknown_everything_cluster()).graph

    payload = json.loads(graph.model_dump_json(by_alias=True))

    assert payload["validation"]["servicePodMappingAccuracy"] is None
    assert payload["validation"]["podNodeMappingAccuracy"] is None
    assert payload["validation"]["concentrationDetectionAccuracy"] is None
    assert isinstance(payload["validation"]["runtimeOverheadMs"], (int, float))
    assert payload["nodes"][0]["zone"] is None
    assert payload["nodes"][0]["cpuAvailable"] is None
    assert payload["services"][0]["pods"][0]["restartCount"] is None
    assert payload["services"][0]["pods"][0]["zone"] is None


def test_a_graph_round_trips_through_json_with_unknowns_intact():
    graph = discover("ns", source=unknown_everything_cluster()).graph

    again = DeploymentGraph.model_validate_json(graph.model_dump_json(by_alias=True))

    assert again == graph


def test_payloads_written_before_the_change_still_validate():
    old = json.loads(SAMPLE_DEPLOYMENT.read_text())
    # A payload written before accuracy became optional carries concrete numbers. The fixture on disk no
    # longer does (Member 2 made it null), so state the old shape here instead of relying on the file.
    old["validation"]["service_pod_mapping_accuracy"] = 1.0

    graph = DeploymentGraph.model_validate(old)

    assert graph.validation.service_pod_mapping_accuracy == 1.0


def test_omitted_accuracy_and_restart_count_default_to_unknown_not_to_a_perfect_score():
    assert ValidationMetrics().service_pod_mapping_accuracy is None
    assert ValidationMetrics().pod_node_mapping_accuracy is None
    assert ValidationMetrics().concentration_detection_accuracy is None
    assert PodModel(name="p", node="n", ready=True).restart_count is None


def test_an_observed_zero_stays_zero():
    assert PodModel(name="p", node="n", ready=True, restart_count=0).restart_count == 0
    assert ValidationMetrics(service_pod_mapping_accuracy=0.0).service_pod_mapping_accuracy == 0.0


# ---- downstream consumers (Components 3 and 4) --------------------------------------------------------------

def test_failure_prediction_runs_on_a_graph_whose_optional_facts_are_unknown():
    graph = discover("ns", source=unknown_everything_cluster()).graph

    report = predict_failure_impact(workflow(), graph, "w1")

    assert report.failed_node == "w1"
    assert report.failed_services == ["api"]  # both api replicas are on w1
    assert report.failed_workflows == ["wf"]


def test_failure_prediction_sees_a_single_replica_on_the_failed_node_as_lost():
    graph = discover("ns", source=unknown_everything_cluster()).graph

    report = predict_failure_impact(workflow(), graph, "w2")

    assert report.failed_services == ["db"]
    assert report.failed_workflows == ["wf"]
    assert report.blast_radius > 0


def test_recommendation_evaluation_runs_on_a_graph_whose_optional_facts_are_unknown():
    graph = discover("ns", source=unknown_everything_cluster()).graph
    impact = predict_failure_impact(workflow(), graph, "w1")

    try:
        result = evaluate_first_recommendation(graph, impact)
    except ValueError as error:
        # "No candidates" is a legitimate answer for this topology; a crash on None is not.
        assert "candidate" in str(error).lower() or "unsupported" in str(error).lower()
    else:
        assert result is not None


def test_component_three_zone_handling_accepts_unknown_zones():
    graph = discover("ns", source=unknown_everything_cluster()).graph

    from app.engine.failure_impact_prediction.cross_layer_graph import build_cross_layer_graph

    built = build_cross_layer_graph(workflow(), graph)

    assert not [n for n in built.nodes if n.node_type == "FAILURE_DOMAIN"]  # no zone => no invented domain


@pytest.mark.parametrize("field", ["service_pod_mapping_accuracy", "pod_node_mapping_accuracy",
                                   "concentration_detection_accuracy"])
def test_no_runtime_graph_ever_reports_a_perfect_score(field):
    graph = discover("ns", source=unknown_everything_cluster()).graph

    assert getattr(graph.validation, field) is None
