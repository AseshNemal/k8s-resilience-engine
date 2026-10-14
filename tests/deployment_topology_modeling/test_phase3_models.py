import pytest
from pydantic import ValidationError
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
    ExposureLevel,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    DriftViolation,
    ServiceExposureResult,
    ClusterExposureReport,
    PlacementDriftDiffReport,
    BaselineB2Result,
    ModelAccuracyCertificate,
)


def test_drift_violation_model():
    violation = DriftViolation(
        drift_type=DriftType.TOPOLOGY_SPREAD_DRIFT,
        severity=DriftSeverity.HIGH,
        description="maxSkew violation on hostname",
        expected=1,
        actual=3,
        affected_failure_domain="worker-node-1",
    )
    assert violation.drift_type == DriftType.TOPOLOGY_SPREAD_DRIFT
    assert violation.severity == DriftSeverity.HIGH
    assert violation.expected == 1
    assert violation.actual == 3
    assert violation.affected_failure_domain == "worker-node-1"


def test_service_exposure_result_valid():
    res = ServiceExposureResult(
        service_name="frontend",
        total_replicas=3,
        ready_replicas=3,
        replica_concentration=0.67,
        exposure_level=ExposureLevel.PARTIAL_EXPOSURE,
        exposed_failure_domains=[],
        partial_loss_domains={"node-1": 2, "node-2": 1},
        severity_score=0.35,
    )
    assert res.service_name == "frontend"
    assert res.replica_concentration == pytest.approx(0.67)
    assert res.exposure_level == ExposureLevel.PARTIAL_EXPOSURE
    assert res.partial_loss_domains["node-1"] == 2
    assert res.severity_score == pytest.approx(0.35)


def test_service_exposure_result_validation_constraints():
    # Negative replicas not allowed
    with pytest.raises(ValidationError):
        ServiceExposureResult(
            service_name="bad",
            total_replicas=-1,
            ready_replicas=0,
            replica_concentration=0.0,
            exposure_level=ExposureLevel.RESILIENT,
        )

    # Concentration > 1.0 not allowed
    with pytest.raises(ValidationError):
        ServiceExposureResult(
            service_name="bad",
            total_replicas=2,
            ready_replicas=2,
            replica_concentration=1.5,
            exposure_level=ExposureLevel.FULL_EXPOSURE,
        )


def test_cluster_exposure_report_metrics():
    report = ClusterExposureReport(
        timestamp="2026-10-11T14:00:00Z",
        total_services=10,
        exposed_service_count=3,
        exposure_rate=0.30,
        constraint_compliance_rate=0.70,
        mean_concentration=0.45,
        runtime_overhead_ms=15.5,
    )
    assert report.total_services == 10
    assert report.exposed_service_count == 3
    assert report.exposure_rate == pytest.approx(0.30)
    assert report.constraint_compliance_rate == pytest.approx(0.70)
    assert report.runtime_overhead_ms == pytest.approx(15.5)


def test_placement_drift_diff_report():
    diff = PlacementDriftDiffReport(
        baseline_snapshot_id="snapshot-001",
        target_snapshot_id="snapshot-002",
        total_services_compared=8,
        drifted_services_count=2,
        placement_drift_rate=0.25,
        newly_exposed_services=["checkout"],
        remediated_services=["cart"],
        state_transitions={"checkout": "RESILIENT -> FULL_EXPOSURE"},
    )
    assert diff.total_services_compared == 8
    assert diff.drifted_services_count == 2
    assert diff.placement_drift_rate == pytest.approx(0.25)
    assert "checkout" in diff.newly_exposed_services
    assert "cart" in diff.remediated_services


def test_baseline_b2_result():
    b2 = BaselineB2Result(
        service_rankings=[
            {"service_name": "payment", "exposure_score": 1.0, "exposed_nodes": ["node-1"]},
            {"service_name": "cart", "exposure_score": 0.5, "exposed_nodes": []},
        ],
        most_vulnerable_service="payment",
        most_critical_worker_node="node-1",
        total_single_points_of_failure=1,
    )
    assert b2.most_vulnerable_service == "payment"
    assert b2.most_critical_worker_node == "node-1"
    assert b2.total_single_points_of_failure == 1


def test_model_accuracy_certificate():
    cert = ModelAccuracyCertificate(
        service_pod_mapping_accuracy=0.99,
        pod_node_mapping_accuracy=1.00,
        owner_resolution_accuracy=0.98,
        concentration_detection_accuracy=0.97,
        is_statistically_sound=True,
    )
    assert cert.service_pod_mapping_accuracy == pytest.approx(0.99)
    assert cert.pod_node_mapping_accuracy == pytest.approx(1.00)
    assert cert.owner_resolution_accuracy == pytest.approx(0.98)
    assert cert.concentration_detection_accuracy == pytest.approx(0.97)
    assert cert.is_statistically_sound is True
