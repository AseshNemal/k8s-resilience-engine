import pytest
from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import analyze_replica_concentration


def _workload(*pods):
    return {"svc": {"name": "svc", "declaredReplicas": len(pods), "namespace": "ns",
                    "deploymentNames": ["svc"], "pods": list(pods)}}


def test_two_one_split():
    # Hand-computed: 3 ready pods, 2 on node-a -> ratio 2/3; no node holds all -> not concentrated.
    (res,) = analyze_replica_concentration(_workload(
        {"name": "p1", "node": "node-a", "ready": True},
        {"name": "p2", "node": "node-a", "ready": True},
        {"name": "p3", "node": "node-b", "ready": True},
    ))
    assert res["replicaConcentration"] == pytest.approx(2 / 3, abs=1e-4)
    assert res["concentrated"] is False
    assert res["exposedNodes"] == []
    assert res["exposureStatus"] == "NOT_EXPOSED"


def test_all_on_one_node():
    (res,) = analyze_replica_concentration(_workload(
        {"name": "p1", "node": "node-db", "ready": True},
        {"name": "p2", "node": "node-db", "ready": True},
    ))
    assert res["replicaConcentration"] == pytest.approx(1.0)
    assert res["concentrated"] is True
    assert res["exposedNodes"] == ["node-db"]


def test_no_ready_pods_is_undefined_not_zero():
    (res,) = analyze_replica_concentration(_workload({"name": "p1", "node": "node-a", "ready": False}))
    assert res["replicaConcentration"] is None
    assert res["concentrated"] is None
    assert res["exposedNodes"] == []
    assert res["exposureStatus"] == "UNAVAILABLE"


def test_keeps_namespace_and_deployment_names_for_manifest_association():
    # Regression: these keys were dropped, so declared intent never reached the detectors.
    (res,) = analyze_replica_concentration(_workload({"name": "p1", "node": "n", "ready": True}))
    assert res["deploymentNames"] == ["svc"]
    assert res["namespace"] == "ns"


def test_not_ready_node_overrides_pod_ready_condition():
    (res,) = analyze_replica_concentration(
        _workload({"name": "p1", "node": "down", "ready": True}, {"name": "p2", "node": "up", "ready": True}),
        node_ready={"down": False, "up": True},
    )
    assert res["readyReplicas"] == 1
    assert res["exposedNodes"] == ["up"]
    assert [p["readyCondition"] for p in res["pods"]] == [True, True]
    assert [p["ready"] for p in res["pods"]] == [False, True]
