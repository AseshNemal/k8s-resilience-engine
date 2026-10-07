import pytest
from unittest.mock import patch, MagicMock
from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import get_cluster_nodes, get_cluster_pods

@patch('app.engine.deployment_topology_modeling.live_state.cluster_state_parser.client.CoreV1Api')
@patch('app.engine.deployment_topology_modeling.live_state.cluster_state_parser.config.load_kube_config')
def test_control_plane_nodes_are_excluded(mock_kube_config, mock_core_api):
    """Test: control-plane nodes are not returned"""
    
    # Setup mock
    mock_api_instance = MagicMock()
    mock_core_api.return_value = mock_api_instance

    # Mock a worker node and a control-plane node
    worker_node = MagicMock()
    worker_node.metadata.name = "worker-1"
    worker_node.metadata.labels = {"topology.kubernetes.io/zone": "zone-a"}
    worker_node.status.allocatable = {"cpu": "4", "memory": "16Gi"}

    cp_node1 = MagicMock()
    cp_node1.metadata.name = "cp-1"
    cp_node1.metadata.labels = {"node-role.kubernetes.io/control-plane": "true"}
    cp_node1.status.allocatable = {"cpu": "4", "memory": "16Gi"}

    cp_node2 = MagicMock()
    cp_node2.metadata.name = "cp-2"
    cp_node2.metadata.labels = {"node-role.kubernetes.io/master": "true"}
    cp_node2.status.allocatable = {"cpu": "4", "memory": "16Gi"}

    mock_node_list = MagicMock()
    mock_node_list.items = [worker_node, cp_node1, cp_node2]
    mock_api_instance.list_node.return_value = mock_node_list

    # Execute
    nodes = get_cluster_nodes()

    # Assert
    assert len(nodes) == 1
    assert nodes[0].name == "worker-1"


@patch('app.engine.deployment_topology_modeling.live_state.cluster_state_parser.client.CoreV1Api')
@patch('app.engine.deployment_topology_modeling.live_state.cluster_state_parser.config.load_kube_config')
def test_pod_readiness_mapped_correctly(mock_kube_config, mock_core_api):
    """Test: pod readiness mapped correctly from conditions"""

    # Setup mock
    mock_api_instance = MagicMock()
    mock_core_api.return_value = mock_api_instance

    # Pod 1: Ready is True
    ready_pod = MagicMock()
    ready_pod.metadata.name = "pod-ready"
    ready_pod.spec.node_name = "node-1"
    ready_cond = MagicMock()
    ready_cond.type = "Ready"
    ready_cond.status = "True"
    ready_pod.status.conditions = [ready_cond]
    ready_pod.metadata.owner_references = None
    ready_pod.metadata.labels = {}

    # Pod 2: Ready is False
    unready_pod = MagicMock()
    unready_pod.metadata.name = "pod-unready"
    unready_pod.spec.node_name = "node-1"
    unready_cond = MagicMock()
    unready_cond.type = "Ready"
    unready_cond.status = "False"
    unready_pod.status.conditions = [unready_cond]
    unready_pod.metadata.owner_references = None
    unready_pod.metadata.labels = {}

    # Pod 3: No conditions array
    no_cond_pod = MagicMock()
    no_cond_pod.metadata.name = "pod-noconds"
    no_cond_pod.spec.node_name = "node-2"
    no_cond_pod.status.conditions = None
    no_cond_pod.metadata.owner_references = None
    no_cond_pod.metadata.labels = {}

    mock_pod_list = MagicMock()
    mock_pod_list.items = [ready_pod, unready_pod, no_cond_pod]
    mock_api_instance.list_namespaced_pod.return_value = mock_pod_list

    # Execute
    pods = get_cluster_pods()

    # Assert
    assert len(pods) == 3
    assert pods[0].name == "pod-ready" and pods[0].ready is True
    assert pods[1].name == "pod-unready" and pods[1].ready is False
    assert pods[2].name == "pod-noconds" and pods[2].ready is False
