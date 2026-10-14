import pytest
from app.engine.deployment_topology_modeling.drift_analysis.failure_domain_exposure_engine import (
    FailureDomainExposureEngine,
)
from app.engine.deployment_topology_modeling.drift_analysis.constants import ExposureLevel


def test_failure_domain_exposure_single_replica():
    engine = FailureDomainExposureEngine()
    runtime_pods = [{"name": "cart-1", "is_ready": True, "node_name": "worker-1"}]
    res = engine.evaluate_service_exposure("cart", runtime_pods)
    assert res.service_name == "cart"
    assert res.ready_replicas == 1
    assert res.exposure_level == ExposureLevel.SINGLE_REPLICA_EXPOSURE
    assert res.replica_concentration == pytest.approx(1.0)
    assert "worker-1" in res.exposed_failure_domains
    assert res.severity_score > 0.5


def test_failure_domain_exposure_co_located_multi_replica():
    # 2 replicas, both on worker-1 -> loss of worker-1 leaves 0 ready replicas
    engine = FailureDomainExposureEngine()
    runtime_pods = [
        {"name": "pay-1", "is_ready": True, "node_name": "worker-1"},
        {"name": "pay-2", "is_ready": True, "node_name": "worker-1"},
    ]
    res = engine.evaluate_service_exposure("payment", runtime_pods)
    assert res.exposure_level == ExposureLevel.FULL_EXPOSURE
    assert res.replica_concentration == pytest.approx(1.0)
    assert res.exposed_failure_domains == ["worker-1"]
    assert res.partial_loss_domains["worker-1"] == 2


def test_failure_domain_exposure_partial_exposure():
    # 3 replicas: 2 on worker-1, 1 on worker-2 -> concentration = 2/3 = 0.67 >= 0.50
    engine = FailureDomainExposureEngine()
    runtime_pods = [
        {"name": "ui-1", "is_ready": True, "node_name": "worker-1"},
        {"name": "ui-2", "is_ready": True, "node_name": "worker-1"},
        {"name": "ui-3", "is_ready": True, "node_name": "worker-2"},
    ]
    res = engine.evaluate_service_exposure("ui", runtime_pods)
    assert res.exposure_level == ExposureLevel.PARTIAL_EXPOSURE
    assert res.replica_concentration == pytest.approx(0.6667, rel=1e-3)
    assert len(res.exposed_failure_domains) == 0
    assert res.partial_loss_domains["worker-1"] == 2
    assert res.partial_loss_domains["worker-2"] == 1


def test_failure_domain_exposure_resilient():
    # 4 replicas across 4 separate nodes -> concentration = 1/4 = 0.25 < 0.50
    engine = FailureDomainExposureEngine()
    runtime_pods = [
        {"name": "cat-1", "is_ready": True, "node_name": "worker-1"},
        {"name": "cat-2", "is_ready": True, "node_name": "worker-2"},
        {"name": "cat-3", "is_ready": True, "node_name": "worker-3"},
        {"name": "cat-4", "is_ready": True, "node_name": "worker-4"},
    ]
    res = engine.evaluate_service_exposure("catalog", runtime_pods)
    assert res.exposure_level == ExposureLevel.RESILIENT
    assert res.replica_concentration == pytest.approx(0.25)
    assert len(res.exposed_failure_domains) == 0
