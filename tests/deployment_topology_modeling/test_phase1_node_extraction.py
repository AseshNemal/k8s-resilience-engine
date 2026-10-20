import pytest
from unittest.mock import MagicMock, patch

from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import (
    get_cluster_pods,
    nodes_from_objects,
    pods_from_objects,
)
from tests.deployment_topology_modeling import kind_snapshot


def _node(name, labels=None, ready="True", unschedulable=None, cpu="4", memory="8Gi"):
    return {
        "metadata": {"name": name, "labels": labels or {}},
        "spec": {"unschedulable": unschedulable} if unschedulable is not None else {},
        "status": {"allocatable": {"cpu": cpu, "memory": memory},
                   "conditions": [{"type": "Ready", "status": ready}]},
    }


def _pod(name, node, ready="True", phase="Running", requests=None, owner=None):
    return {
        "metadata": {"name": name, "namespace": "ns", "labels": {"app": "x"},
                     "ownerReferences": [owner] if owner else []},
        "spec": {"nodeName": node, "containers": [{"resources": {"requests": requests or {}}}]},
        "status": {"phase": phase, "conditions": [{"type": "Ready", "status": ready}],
                   "containerStatuses": [{"restartCount": 2}]},
    }


def test_control_plane_nodes_are_excluded():
    nodes = nodes_from_objects([
        _node("worker-1"),
        _node("cp-1", {"node-role.kubernetes.io/control-plane": ""}),
        _node("cp-2", {"node-role.kubernetes.io/master": "true"}),
    ], [])
    assert [n.name for n in nodes] == ["worker-1"]


def test_real_kind_snapshot_nodes():
    nodes = nodes_from_objects(kind_snapshot.node_objects(), kind_snapshot.pod_objects())
    assert [n.name for n in nodes] == [
        "resilience-research-worker", "resilience-research-worker2", "resilience-research-worker3",
    ]
    assert all(n.ready is True and n.schedulable for n in nodes)
    assert nodes[0].labels["research-node"] == "worker-1"
    assert nodes[0].zone is None  # KinD nodes have no zone label; none is invented
    # Hand-computed: allocatable 8 CPU, 4010356Ki memory; 3 pods x (50m CPU, 32Mi) requested.
    assert float(nodes[0].cpu_available) == pytest.approx(8 - 3 * 0.05)
    assert float(nodes[0].memory_available) == pytest.approx(4010356 * 1024 - 3 * 32 * 1024 ** 2)
    assert float(nodes[1].cpu_available) == pytest.approx(8.0)


def test_node_ready_and_cordon_state():
    nodes = nodes_from_objects([_node("a", ready="Unknown"), _node("b", unschedulable=True)], [])
    assert nodes[0].ready is False
    assert nodes[1].schedulable is False


def test_terminal_pods_do_not_consume_capacity():
    pods = [_pod("done", "a", phase="Succeeded", requests={"cpu": "1"}),
            _pod("run", "a", requests={"cpu": "500m"})]
    (node,) = nodes_from_objects([_node("a")], pods)
    assert float(node.cpu_available) == pytest.approx(3.5)


def test_pod_readiness_phase_and_owner():
    owner = {"kind": "ReplicaSet", "name": "x-rs", "uid": "u1", "controller": True}
    pods = pods_from_objects([
        _pod("ready", "n1", owner=owner),
        _pod("unready", "n1", ready="False"),
        {"metadata": {"name": "noconds"}, "spec": {}, "status": {"phase": "Pending"}},
    ], namespace="ns")
    assert [p.ready for p in pods] == [True, False, False]
    assert pods[0].owner_kind == "ReplicaSet"
    assert pods[0].owner_uid == "u1"
    assert pods[0].restart_count == 2
    assert pods[2].node is None
    assert pods[2].phase == "Pending"


def test_real_kind_snapshot_pods():
    pods = pods_from_objects(kind_snapshot.pod_objects(), "resilience-test")
    assert len(pods) == 3
    assert {p.node for p in pods} == {"resilience-research-worker"}
    assert all(p.ready and p.phase == "Running" and p.owner_name == "paymentservice-74d644658f" for p in pods)


@patch("app.engine.deployment_topology_modeling.live_state.cluster_state_parser.client")
@patch("app.engine.deployment_topology_modeling.live_state.cluster_state_parser._load_kubernetes_config")
def test_get_cluster_pods_converts_api_objects(_mock_config, mock_client):
    api = MagicMock()
    mock_client.CoreV1Api.return_value = api
    mock_client.ApiClient.return_value.sanitize_for_serialization.return_value = kind_snapshot.pod_objects()
    pods = get_cluster_pods("resilience-test")
    api.list_namespaced_pod.assert_called_once_with(namespace="resilience-test")
    assert len(pods) == 3
