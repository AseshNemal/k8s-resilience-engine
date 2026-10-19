import pytest
from app.engine.deployment_topology_modeling.drift_analysis.deployment_only_baseline_engine import (
    DeploymentOnlyBaselineEngine,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import ServiceExposureResult
from app.engine.deployment_topology_modeling.drift_analysis.constants import ExposureLevel


def test_deployment_baseline_b2_ranking_and_critical_node():
    # Setup mock exposures
    exp_cart = ServiceExposureResult(
        service_name="cart",
        total_replicas=2,
        ready_replicas=2,
        replica_concentration=1.0,
        exposure_level=ExposureLevel.FULL_EXPOSURE,
        exposed_failure_domains=["node-1"],
        severity_score=0.75,
    )
    exp_pay = ServiceExposureResult(
        service_name="payment",
        total_replicas=1,
        ready_replicas=1,
        replica_concentration=1.0,
        exposure_level=ExposureLevel.SINGLE_REPLICA_EXPOSURE,
        exposed_failure_domains=["node-1"],
        severity_score=0.90,
    )
    exp_frontend = ServiceExposureResult(
        service_name="frontend",
        total_replicas=3,
        ready_replicas=3,
        replica_concentration=0.33,
        exposure_level=ExposureLevel.RESILIENT,
        exposed_failure_domains=[],
        severity_score=0.10,
    )

    b2 = DeploymentOnlyBaselineEngine.generate_baseline_b2([exp_cart, exp_pay, exp_frontend])

    assert b2.most_vulnerable_service == "payment"
    assert b2.most_critical_worker_node == "node-1"
    assert b2.total_single_points_of_failure == 2
    assert len(b2.service_rankings) == 3

    # Rank 1 must be payment (highest severity score 0.90)
    assert b2.service_rankings[0]["service_name"] == "payment"
    assert b2.service_rankings[0]["rank"] == 1

    # Rank 2 must be cart (severity score 0.75)
    assert b2.service_rankings[1]["service_name"] == "cart"
    assert b2.service_rankings[1]["rank"] == 2

    # Rank 3 must be frontend (severity score 0.10)
    assert b2.service_rankings[2]["service_name"] == "frontend"
    assert b2.service_rankings[2]["rank"] == 3


def test_deployment_baseline_b2_empty():
    b2 = DeploymentOnlyBaselineEngine.generate_baseline_b2([])
    assert b2.most_vulnerable_service is None
    assert b2.most_critical_worker_node is None
    assert b2.total_single_points_of_failure == 0
    assert len(b2.service_rankings) == 0
