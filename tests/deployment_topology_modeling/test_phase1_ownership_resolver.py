import pytest
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import resolve_deployment_ownership
from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterPod
from unittest.mock import patch, MagicMock

@patch("app.engine.deployment_topology_modeling.live_state.ownership_resolver.client.AppsV1Api")
@patch("app.engine.deployment_topology_modeling.live_state.ownership_resolver.config.load_kube_config")
def test_resolve_deployment_ownership_mapping(mock_config, mock_apps_v1):
    # Mock deployments
    mock_dep = MagicMock()
    mock_dep.metadata.name = "web-service"
    mock_dep.spec.replicas = 3
    
    mock_api_instance = MagicMock()
    mock_api_instance.list_namespaced_deployment.return_value.items = [mock_dep]
    mock_apps_v1.return_value = mock_api_instance
    
    # Fake ClusterPods
    fake_pods = [
        ClusterPod(name="web-1", node="node-a", ready=True, owner_kind="ReplicaSet", owner_name="web-service-rs", app_label="web-service", labels={}),
        ClusterPod(name="web-2", node="node-b", ready=False, owner_kind="ReplicaSet", owner_name="web-service-rs", app_label="web-service", labels={}),
        ClusterPod(name="db-1", node="node-c", ready=True, owner_kind="ReplicaSet", owner_name="db-rs", app_label="db-service", labels={}),
    ]
    
    workloads = resolve_deployment_ownership(fake_pods)
    
    assert "web-service" in workloads
    assert workloads["web-service"]["declaredReplicas"] == 3
    assert workloads["web-service"]["readyReplicas"] == 1 # Only web-1 is ready
    assert len(workloads["web-service"]["pods"]) == 2
    
    # db-1 is ignored because there is no matching mock deployment for "db-service"
    assert "db-service" not in workloads
