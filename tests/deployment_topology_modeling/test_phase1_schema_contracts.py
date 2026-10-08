import pytest
from pydantic import ValidationError
from app.shared.schemas.deployment import ServicePlacement, PodModel

def test_service_placement_accepts_valid_drift_reasons():
    """Test: schema accepts empty list and populated list for drift_reasons"""
    
    # Empty list
    model1 = ServicePlacement(
        name="test-service1",
        declared_replicas=3,
        ready_replicas=3,
        pods=[],
        drift_detected=False,
        drift_reasons=[]
    )
    assert model1.drift_reasons == []

    # Populated list
    model2 = ServicePlacement(
        name="test-service2",
        declared_replicas=3,
        ready_replicas=3,
        pods=[],
        drift_detected=True,
        drift_reasons=["Scale drift detected"]
    )
    assert model2.drift_reasons == ["Scale drift detected"]

def test_service_placement_rejects_invalid_drift_reasons_type():
    """Test: schema rejects invalid drift_reasons type"""
    
    with pytest.raises(ValidationError):
        ServicePlacement(
            name="test-service",
            declared_replicas=3,
            ready_replicas=3,
            pods=[],
            drift_detected=True,
            drift_reasons="This should be a list, not a string"
        )
