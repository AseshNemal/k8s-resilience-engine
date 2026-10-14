from app.engine.deployment_topology_modeling.drift_analysis.resource_contention_detector import ResourceContentionDetector
from app.engine.deployment_topology_modeling.drift_analysis.constants import DriftType, DriftSeverity


def test_resource_contention_detector_compliant():
    detector = ResourceContentionDetector()
    declared = {
        "resources": [
            {
                "limits": {"cpu": "500m", "memory": "256Mi"},
                "requests": {"cpu": "100m", "memory": "128Mi"},
            }
        ]
    }
    violations = detector.detect("payment", declared, [], [])
    assert len(violations) == 0
    assert detector.is_compliant(violations) is True


def test_resource_contention_detector_no_resources():
    detector = ResourceContentionDetector()
    declared = {"resources": []}
    violations = detector.detect("wild-svc", declared, [], [])
    assert len(violations) == 1
    assert violations[0].drift_type == DriftType.RESOURCE_CONTENTION_DRIFT
    assert violations[0].severity == DriftSeverity.MEDIUM
    assert "no resource specifications" in violations[0].description


def test_resource_contention_detector_missing_memory_limit():
    detector = ResourceContentionDetector()
    declared = {
        "resources": [
            {
                "limits": {"cpu": "200m"},
                "requests": {"cpu": "100m"},
            }
        ]
    }
    violations = detector.detect("cache", declared, [], [])
    assert len(violations) == 1
    assert violations[0].drift_type == DriftType.RESOURCE_CONTENTION_DRIFT
    assert violations[0].actual == "missing memory"
    assert "missing resource limits: memory" in violations[0].description
