import pytest
from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import analyze_replica_concentration

def test_concentration_math_standard():
    workloads = {
        "web": {
            "name": "web",
            "declaredReplicas": 3,
            "readyReplicas": 3,
            "pods": [
                {"name": "p1", "node": "node-a", "ready": True},
                {"name": "p2", "node": "node-a", "ready": True},
                {"name": "p3", "node": "node-b", "ready": True}
            ]
        }
    }
    
    results = analyze_replica_concentration(workloads)
    assert len(results) == 1
    
    web_res = results[0]
    # Total ready pods = 3. Max on one node (node-a) is 2. Concentration = 2 / 3 = 0.67
    assert web_res["replicaConcentration"] == pytest.approx(0.67, abs=0.01)
    # Neither node-a nor node-b hosts ALL ready replicas, so no exposed nodes
    assert len(web_res["exposedNodes"]) == 0

def test_concentration_math_all_exposed():
    workloads = {
        "db": {
            "name": "db",
            "declaredReplicas": 2,
            "readyReplicas": 2,
            "pods": [
                {"name": "p1", "node": "node-db", "ready": True},
                {"name": "p2", "node": "node-db", "ready": True}
            ]
        }
    }
    
    results = analyze_replica_concentration(workloads)
    db_res = results[0]
    
    assert db_res["replicaConcentration"] == pytest.approx(1.0)
    assert "node-db" in db_res["exposedNodes"]

def test_concentration_math_no_ready_pods():
    workloads = {
        "cache": {
            "name": "cache",
            "declaredReplicas": 1,
            "readyReplicas": 0,
            "pods": [
                {"name": "p1", "node": "node-a", "ready": False}
            ]
        }
    }
    
    results = analyze_replica_concentration(workloads)
    res = results[0]
    assert res["replicaConcentration"] == 0.0
    assert len(res["exposedNodes"]) == 0
