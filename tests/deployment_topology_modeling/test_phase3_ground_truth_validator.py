import pytest
from app.engine.deployment_topology_modeling.drift_analysis.ground_truth_validator import (
    GroundTruthValidator,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import ServiceExposureResult
from app.engine.deployment_topology_modeling.drift_analysis.constants import ExposureLevel


def test_ground_truth_validator_perfect_accuracy():
    # 100% agreement between predicted and synthetic ground truth
    pred_pod_svc = {"pod-1": "auth", "pod-2": "auth", "pod-3": "cart"}
    gt_pod_svc = {"pod-1": "auth", "pod-2": "auth", "pod-3": "cart"}

    pred_pod_node = {"pod-1": "node-1", "pod-2": "node-2", "pod-3": "node-1"}
    gt_pod_node = {"pod-1": "node-1", "pod-2": "node-2", "pod-3": "node-1"}

    pred_owner = {"rs-auth": "deploy-auth", "rs-cart": "deploy-cart"}
    gt_owner = {"rs-auth": "deploy-auth", "rs-cart": "deploy-cart"}

    exp_auth = ServiceExposureResult(
        service_name="auth",
        total_replicas=2,
        ready_replicas=2,
        replica_concentration=0.5,
        exposure_level=ExposureLevel.RESILIENT,
        exposed_failure_domains=[],
    )
    exp_cart = ServiceExposureResult(
        service_name="cart",
        total_replicas=1,
        ready_replicas=1,
        replica_concentration=1.0,
        exposure_level=ExposureLevel.SINGLE_REPLICA_EXPOSURE,
        exposed_failure_domains=["node-1"],
    )

    cert = GroundTruthValidator.evaluate_mapping_accuracies(
        predicted_pod_service_map=pred_pod_svc,
        ground_truth_pod_service_map=gt_pod_svc,
        predicted_pod_node_map=pred_pod_node,
        ground_truth_pod_node_map=gt_pod_node,
        predicted_owner_map=pred_owner,
        ground_truth_owner_map=gt_owner,
        predicted_exposures=[exp_auth, exp_cart],
        ground_truth_exposed_services={"cart"},
    )

    assert cert.service_pod_mapping_accuracy == pytest.approx(1.0)
    assert cert.pod_node_mapping_accuracy == pytest.approx(1.0)
    assert cert.owner_resolution_accuracy == pytest.approx(1.0)
    assert cert.concentration_detection_accuracy == pytest.approx(1.0)
    assert cert.is_statistically_sound is True


def test_ground_truth_validator_degraded_accuracy():
    # Only 1 of 2 pods correctly mapped
    pred_pod_svc = {"pod-1": "auth", "pod-2": "unknown"}
    gt_pod_svc = {"pod-1": "auth", "pod-2": "auth"}

    cert = GroundTruthValidator.evaluate_mapping_accuracies(
        predicted_pod_service_map=pred_pod_svc,
        ground_truth_pod_service_map=gt_pod_svc,
        predicted_pod_node_map={},
        ground_truth_pod_node_map={},
        predicted_owner_map={},
        ground_truth_owner_map={},
        predicted_exposures=[],
        ground_truth_exposed_services=set(),
    )

    assert cert.service_pod_mapping_accuracy == pytest.approx(0.50)
    assert cert.is_statistically_sound is False  # 0.50 < 0.95 threshold
