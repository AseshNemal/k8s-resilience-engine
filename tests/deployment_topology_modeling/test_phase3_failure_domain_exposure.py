import pytest
from app.engine.deployment_topology_modeling.placement_metrics import ExposureStatus
from app.engine.deployment_topology_modeling.drift_analysis.failure_domain_exposure_engine import (
    FailureDomainExposureEngine,
)
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftSeverity,
    DriftType,
    ExposureLevel,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import DriftViolation


def evaluate(pods, **kwargs):
    return FailureDomainExposureEngine().evaluate_service_exposure("svc", pods, **kwargs)


def test_single_replica_is_exposed():
    # Study Guide section 9 Step 6: "Inventory 1 replica on worker-c -> exposed due to single replica".
    res = evaluate([{"name": "cart-1", "is_ready": True, "node_name": "worker-1"}])
    assert res.exposure_status == ExposureStatus.EXPOSED
    assert res.exposure_level == ExposureLevel.SINGLE_REPLICA_EXPOSURE
    assert res.exposed_failure_domains == ["worker-1"]
    assert res.concentrated is True
    assert res.replica_concentration == pytest.approx(1.0)


def test_two_replicas_on_one_node_fully_exposed():
    # Study Guide: Payment, 2 replicas on worker-a -> fully exposed to worker-a.
    res = evaluate([
        {"name": "pay-1", "is_ready": True, "node_name": "worker-a"},
        {"name": "pay-2", "is_ready": True, "node_name": "worker-a"},
    ])
    assert res.exposure_level == ExposureLevel.FULL_EXPOSURE
    assert res.exposed_failure_domains == ["worker-a"]
    assert res.ready_replicas_per_node == {"worker-a": 2}


def test_two_replicas_on_two_nodes_is_not_exposed():
    # Study Guide: Order, worker-a + worker-b -> not fully exposed.
    # (Old code classified this as PARTIAL_EXPOSURE because 1/2 >= 0.50.)
    res = evaluate([
        {"name": "o-1", "is_ready": True, "node_name": "worker-a"},
        {"name": "o-2", "is_ready": True, "node_name": "worker-b"},
    ])
    assert res.exposure_status == ExposureStatus.NOT_EXPOSED
    assert res.exposure_level == ExposureLevel.RESILIENT
    assert res.concentrated is False
    assert res.replica_concentration == pytest.approx(0.5)
    assert res.exposed_failure_domains == []


def test_two_one_split_not_exposed_ratio_two_thirds():
    res = evaluate([
        {"name": "ui-1", "is_ready": True, "node_name": "worker-1"},
        {"name": "ui-2", "is_ready": True, "node_name": "worker-1"},
        {"name": "ui-3", "is_ready": True, "node_name": "worker-2"},
    ])
    assert res.exposure_status == ExposureStatus.NOT_EXPOSED
    assert res.replica_concentration == pytest.approx(0.6667, abs=1e-4)


def test_unready_replicas_do_not_protect_a_service():
    # 1 ready on worker-1, 1 NOT ready on worker-2: losing worker-1 leaves 0 ready replicas.
    res = evaluate([
        {"name": "a", "is_ready": True, "node_name": "worker-1"},
        {"name": "b", "is_ready": False, "node_name": "worker-2"},
    ])
    assert res.total_replicas == 2
    assert res.ready_replicas == 1
    assert res.exposed_failure_domains == ["worker-1"]


def test_no_ready_replicas_is_unavailable_not_exposed():
    res = evaluate([{"name": "a", "is_ready": False, "node_name": "worker-1"}])
    assert res.exposure_status == ExposureStatus.UNAVAILABLE
    assert res.exposure_level == ExposureLevel.UNAVAILABLE
    assert res.concentrated is None
    assert res.replica_concentration is None
    assert res.exposed_failure_domains == []


def test_ready_pod_without_node_is_not_a_placed_replica():
    res = evaluate([{"name": "a", "is_ready": True, "node_name": None}])
    assert res.exposure_status == ExposureStatus.UNAVAILABLE


def test_empty_service():
    res = evaluate([])
    assert res.exposure_status == ExposureStatus.UNAVAILABLE
    assert res.total_replicas == 0


def test_placement_rules_satisfied_ignores_informational_findings():
    info = DriftViolation(drift_type=DriftType.RESOURCE_CONTENTION_DRIFT, severity=DriftSeverity.LOW, description="x")
    spread = DriftViolation(drift_type=DriftType.TOPOLOGY_SPREAD_DRIFT, severity=DriftSeverity.HIGH, description="y")
    pods = [{"name": "a", "ready": True, "node": "n1"}]
    assert evaluate(pods, violations=[info], has_placement_rules=True).placement_rules_satisfied is True
    assert evaluate(pods, violations=[spread], has_placement_rules=True).placement_rules_satisfied is False
    assert evaluate(pods, violations=[spread], has_placement_rules=False).placement_rules_satisfied is None
