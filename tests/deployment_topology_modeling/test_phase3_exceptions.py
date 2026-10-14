import pytest
from app.engine.deployment_topology_modeling.drift_analysis.exceptions import (
    DriftAnalysisError,
    ExposureEvaluationError,
    InvalidTopologyConstraintError,
    DriftThresholdExceededError,
)


def test_base_drift_analysis_error():
    err = DriftAnalysisError("Core drift error", service_name="order-service")
    assert str(err) == "Core drift error"
    assert err.service_name == "order-service"
    assert isinstance(err, Exception)


def test_exposure_evaluation_error():
    err = ExposureEvaluationError("Failed to simulate node failure", service_name="payment")
    assert isinstance(err, DriftAnalysisError)
    assert err.service_name == "payment"
    assert "node failure" in str(err)


def test_invalid_topology_constraint_error():
    err = InvalidTopologyConstraintError("Invalid syntax in topologySpreadConstraints")
    assert isinstance(err, DriftAnalysisError)
    assert err.service_name is None


def test_drift_threshold_exceeded_error():
    err = DriftThresholdExceededError("Concentration threshold exceeded 0.5")
    assert isinstance(err, DriftAnalysisError)
    with pytest.raises(DriftAnalysisError):
        raise err
