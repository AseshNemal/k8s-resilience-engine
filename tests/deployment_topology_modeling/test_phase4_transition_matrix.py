import pytest
from experiments.placement_drift.cluster_state_freezer import ClusterStateFreezer
from experiments.placement_drift.drift_transition_matrix import DriftTransitionMatrix
from experiments.placement_drift.scenario_models import ClusterSnapshot, PodPlacementRecord


def test_drift_transition_matrix_build():
    t0_pods = [
        PodPlacementRecord(pod_name="p1", service_name="svc-a", node_name="node-1"),
        PodPlacementRecord(pod_name="p2", service_name="svc-b", node_name="node-2"),
    ]
    t1_pods = [
        # p1 migrated from node-1 to node-2
        PodPlacementRecord(pod_name="p1", service_name="svc-a", node_name="node-2"),
        # p2 stayed on node-2
        PodPlacementRecord(pod_name="p2", service_name="svc-b", node_name="node-2"),
    ]

    t0_snap = ClusterStateFreezer.freeze("snap-t0", t0_pods, [{"name": "node-1"}, {"name": "node-2"}])
    t1_snap = ClusterStateFreezer.freeze("snap-t1", t1_pods, [{"name": "node-1"}, {"name": "node-2"}])

    nodes, matrix, records = DriftTransitionMatrix.build_matrix(t0_snap, t1_snap)

    assert "node-1" in nodes
    assert "node-2" in nodes
    # node-1 -> node-2 has 1 migration (p1)
    assert matrix["node-1"]["node-2"] == 1
    # node-2 -> node-2 has 1 non-migration (p2 stayed)
    assert matrix["node-2"]["node-2"] == 1
    assert matrix["node-1"]["node-1"] == 0

    assert len(records) == 1
    assert records[0].from_node == "node-1"
    assert records[0].to_node == "node-2"
    assert records[0].pod_count == 1
    assert "svc-a" in records[0].services_affected


def test_drift_transition_matrix_format_ascii():
    nodes = ["node-1", "node-2"]
    matrix = {
        "node-1": {"node-1": 0, "node-2": 2},
        "node-2": {"node-1": 0, "node-2": 3},
    }
    ascii_table = DriftTransitionMatrix.format_ascii_table(nodes, matrix)
    assert "From \\ To" in ascii_table
    assert "node-1" in ascii_table
    assert "node-2" in ascii_table


def test_drift_transition_matrix_format_latex():
    nodes = ["node-1", "node-2"]
    matrix = {
        "node-1": {"node-1": 0, "node-2": 1},
        "node-2": {"node-1": 0, "node-2": 2},
    }
    latex_table = DriftTransitionMatrix.format_latex_table(nodes, matrix)
    assert r"\begin{table}" in latex_table
    assert r"\begin{tabular}" in latex_table
    assert "node-1 & 0 & 1" in latex_table
    assert r"\end{table}" in latex_table
