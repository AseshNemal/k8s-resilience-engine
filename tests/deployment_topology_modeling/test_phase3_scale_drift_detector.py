from app.engine.deployment_topology_modeling.drift_analysis.scale_drift_detector import ScaleDriftDetector
from app.engine.deployment_topology_modeling.drift_analysis.constants import DriftType, DriftSeverity


def test_scale_drift_detector_aligned():
    detector = ScaleDriftDetector()
    declared = {"replicas": 2}
    runtime_pods = [
        {"name": "cart-1", "is_ready": True, "node": "node-1"},
        {"name": "cart-2", "is_ready": True, "node": "node-2"},
    ]
    violations = detector.detect("cart", declared, runtime_pods, [])
    assert len(violations) == 0
    assert detector.is_compliant(violations) is True


def test_scale_drift_detector_degraded_capacity():
    detector = ScaleDriftDetector()
    declared = {"replicas": 3}
    runtime_pods = [
        {"name": "order-1", "is_ready": True, "node": "node-1"},
        {"name": "order-2", "is_ready": False, "node": "node-2"},
    ]
    violations = detector.detect("order", declared, runtime_pods, [])
    assert len(violations) == 1
    assert violations[0].drift_type == DriftType.SCALE_DRIFT
    assert violations[0].severity == DriftSeverity.HIGH
    assert violations[0].expected == 3
    assert violations[0].actual == 1
    assert detector.is_compliant(violations) is False


def test_scale_drift_detector_zero_ready_critical():
    detector = ScaleDriftDetector()
    declared = {"replicas": 2}
    runtime_pods = [
        {"name": "payment-1", "is_ready": False, "node": "node-1"},
        {"name": "payment-2", "is_ready": False, "node": "node-1"},
    ]
    violations = detector.detect("payment", declared, runtime_pods, [])
    assert len(violations) == 1
    assert violations[0].severity == DriftSeverity.CRITICAL
    assert violations[0].actual == 0


def test_scale_drift_detector_overscaled():
    detector = ScaleDriftDetector()
    declared = {"replicas": 1}
    runtime_pods = [
        {"name": "frontend-1", "is_ready": True, "node": "node-1"},
        {"name": "frontend-2", "is_ready": True, "node": "node-2"},
    ]
    violations = detector.detect("frontend", declared, runtime_pods, [])
    assert len(violations) == 1
    assert violations[0].severity == DriftSeverity.LOW
    assert violations[0].actual == 2
