import pytest
from pydantic import ValidationError
from app.engine.deployment_topology_modeling.placement_metrics import ExposureStatus
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
    assert violation.expected == 1
    assert violation.actual == 3
    assert violation.affected_failure_domain == "worker-node-1"


def test_service_exposure_result_valid():
    res = ServiceExposureResult(
        service_name="frontend",
        total_replicas=3,
        ready_replicas=3,
        exposure_status=ExposureStatus.NOT_EXPOSED,
        exposure_level=ExposureLevel.RESILIENT,
        concentrated=False,
        replica_concentration=0.6667,
        ready_replicas_per_node={"node-1": 2, "node-2": 1},
    )
    assert res.replica_concentration == pytest.approx(2 / 3, abs=1e-4)
    assert res.ready_replicas_per_node["node-1"] == 2
    assert res.placement_rules_satisfied is None


def test_service_exposure_result_validation_constraints():
    with pytest.raises(ValidationError):
        ServiceExposureResult(
            service_name="bad", total_replicas=-1, ready_replicas=0,
            exposure_status=ExposureStatus.UNAVAILABLE, exposure_level=ExposureLevel.UNAVAILABLE,
        )
    with pytest.raises(ValidationError):
        ServiceExposureResult(
            service_name="bad", total_replicas=2, ready_replicas=2, replica_concentration=1.5,
            exposure_status=ExposureStatus.EXPOSED, exposure_level=ExposureLevel.FULL_EXPOSURE,
        )


def test_cluster_exposure_report_allows_unmeasurable_rates():
    report = ClusterExposureReport(
        timestamp="2026-10-11T14:00:00Z",
        total_services=10,
        evaluated_services=10,
        exposed_service_count=3,
        exposure_rate=0.30,
    )
    assert report.exposure_rate == pytest.approx(0.30)
    # No services with rules -> compliance is not measurable, not "100%".
    assert report.constraint_compliance_rate is None


def test_placement_drift_diff_report():
    diff = PlacementDriftDiffReport(
        baseline_snapshot_id="snapshot-001",
        target_snapshot_id="snapshot-002",
        total_services_compared=8,
        drifted_services_count=2,
        placement_drift_rate=0.25,
        newly_exposed_services=["checkout"],
        remediated_services=["cart"],
    )
    assert diff.placement_drift_rate == pytest.approx(0.25)
    assert diff.added_services == [] and diff.removed_services == []


def test_baseline_b2_result():
    b2 = BaselineB2Result(most_vulnerable_service="payment", most_critical_worker_node="node-1",
                          exposed_services_by_node={"node-1": ["payment"]}, total_single_points_of_failure=1)
    assert b2.exposed_services_by_node == {"node-1": ["payment"]}


def test_model_accuracy_certificate_defaults_to_not_measured():
    cert = ModelAccuracyCertificate()
    assert cert.service_pod_mapping_accuracy is None
    assert cert.exposure_detection_accuracy is None
    assert cert.is_statistically_sound is False
