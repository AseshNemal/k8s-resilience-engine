import pytest
from app.engine.deployment_topology_modeling.drift_analysis.placement_drift_diff_engine import (
    PlacementDriftDiffEngine,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    ServiceExposureResult,
    ClusterExposureReport,
)
from app.engine.deployment_topology_modeling.drift_analysis.constants import ExposureLevel


def test_placement_drift_diff_engine_no_drift():
    # Both snapshots have identical placement
    exp = ServiceExposureResult(
        service_name="payment",
        total_replicas=2,
        ready_replicas=2,
        replica_concentration=0.5,
        exposure_level=ExposureLevel.RESILIENT,
        exposed_failure_domains=[],
    )
    snap0 = ClusterExposureReport(
        timestamp="T0",
        total_services=1,
        exposed_service_count=0,
        exposure_rate=0.0,
        constraint_compliance_rate=1.0,
        mean_concentration=0.5,
        services=[exp],
    )
    snap1 = ClusterExposureReport(
        timestamp="T1",
        total_services=1,
        exposed_service_count=0,
        exposure_rate=0.0,
        constraint_compliance_rate=1.0,
        mean_concentration=0.5,
        services=[exp],
    )

    diff = PlacementDriftDiffEngine.diff_snapshots(snap0, snap1)
    assert diff.total_services_compared == 1
    assert diff.drifted_services_count == 0
    assert diff.placement_drift_rate == pytest.approx(0.0)
    assert len(diff.newly_exposed_services) == 0
    assert len(diff.remediated_services) == 0


def test_placement_drift_diff_engine_newly_exposed_and_remediated():
    # T0: payment was RESILIENT, cart was FULL_EXPOSURE
    pay_t0 = ServiceExposureResult(
        service_name="payment",
        total_replicas=2,
        ready_replicas=2,
        replica_concentration=0.5,
        exposure_level=ExposureLevel.RESILIENT,
        exposed_failure_domains=[],
    )
    cart_t0 = ServiceExposureResult(
        service_name="cart",
        total_replicas=2,
        ready_replicas=2,
        replica_concentration=1.0,
        exposure_level=ExposureLevel.FULL_EXPOSURE,
        exposed_failure_domains=["node-1"],
    )

    # T1: after node eviction, payment co-located on node-2 (now FULL_EXPOSURE),
    # cart was re-balanced across nodes (now RESILIENT)
    pay_t1 = ServiceExposureResult(
        service_name="payment",
        total_replicas=2,
        ready_replicas=2,
        replica_concentration=1.0,
        exposure_level=ExposureLevel.FULL_EXPOSURE,
        exposed_failure_domains=["node-2"],
    )
    cart_t1 = ServiceExposureResult(
        service_name="cart",
        total_replicas=2,
        ready_replicas=2,
        replica_concentration=0.5,
        exposure_level=ExposureLevel.RESILIENT,
        exposed_failure_domains=[],
    )

    snap0 = {"services": [pay_t0, cart_t0]}
    snap1 = {"services": [pay_t1, cart_t1]}

    diff = PlacementDriftDiffEngine.diff_snapshots(snap0, snap1)
    assert diff.total_services_compared == 2
    assert diff.drifted_services_count == 2
    assert diff.placement_drift_rate == pytest.approx(1.0)
    assert diff.newly_exposed_services == ["payment"]
    assert diff.remediated_services == ["cart"]
    assert diff.state_transitions["payment"] == "RESILIENT -> FULL_EXPOSURE"
    assert diff.state_transitions["cart"] == "FULL_EXPOSURE -> RESILIENT"
