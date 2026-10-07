import pytest
from app.engine.deployment_topology_modeling.graph_builder import DeploymentGraphBuilder
from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterNode

def test_graph_builder_node_parsing():
    builder = DeploymentGraphBuilder()
    
    fake_nodes = [
        ClusterNode(name="worker-1", zone="us-east", cpu_available="2000m", memory_available="4Gi"),
        ClusterNode(name="worker-2", zone="us-east", cpu_available="4", memory_available="8388608Ki"), # 8Gi
    ]
    
    builder.attach_nodes(fake_nodes)
    
    assert len(builder.node_models) == 2
    assert builder.node_models[0].cpu_available == pytest.approx(2.0)
    assert builder.node_models[0].memory_available == pytest.approx(4.0)
    
    assert builder.node_models[1].cpu_available == pytest.approx(4.0)
    assert builder.node_models[1].memory_available == pytest.approx(8.0)
