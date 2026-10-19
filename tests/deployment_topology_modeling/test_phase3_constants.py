import pytest
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
    FailureDomainType,
    ExposureLevel,
    SEVERITY_WEIGHT_FULL_EXPOSURE,
    SEVERITY_WEIGHT_CONCENTRATION,
    SEVERITY_WEIGHT_SCALE_MISMATCH,
    SEVERITY_WEIGHT_RESOURCE_UNBOUNDED,
    TARGET_MAPPING_ACCURACY,
    TARGET_CONCENTRATION_ACCURACY,
)


def test_drift_type_enum_members():
    assert DriftType.SCALE_DRIFT.value == "SCALE_DRIFT"
    assert DriftType.AFFINITY_DRIFT.value == "AFFINITY_DRIFT"
    assert DriftType.TOPOLOGY_SPREAD_DRIFT.value == "TOPOLOGY_SPREAD_DRIFT"
    assert DriftType.RESOURCE_CONTENTION_DRIFT.value == "RESOURCE_CONTENTION_DRIFT"
    assert len(DriftType) == 4


def test_drift_severity_tiers():
    assert DriftSeverity.LOW.value == "LOW"
    assert DriftSeverity.MEDIUM.value == "MEDIUM"
    assert DriftSeverity.HIGH.value == "HIGH"
    assert DriftSeverity.CRITICAL.value == "CRITICAL"
    assert len(DriftSeverity) == 4


def test_failure_domain_keys():
    assert FailureDomainType.HOSTNAME.value == "kubernetes.io/hostname"
    assert FailureDomainType.ZONE.value == "topology.kubernetes.io/zone"
    assert FailureDomainType.REGION.value == "topology.kubernetes.io/region"


def test_exposure_levels():
    assert ExposureLevel.FULL_EXPOSURE.value == "FULL_EXPOSURE"
    assert ExposureLevel.SINGLE_REPLICA_EXPOSURE.value == "SINGLE_REPLICA_EXPOSURE"
    assert ExposureLevel.PARTIAL_EXPOSURE.value == "PARTIAL_EXPOSURE"
    assert ExposureLevel.RESILIENT.value == "RESILIENT"


def test_scoring_weights_sum_to_one():
    total_weight = (
        SEVERITY_WEIGHT_FULL_EXPOSURE
        + SEVERITY_WEIGHT_CONCENTRATION
        + SEVERITY_WEIGHT_SCALE_MISMATCH
        + SEVERITY_WEIGHT_RESOURCE_UNBOUNDED
    )
    assert total_weight == pytest.approx(1.0)


def test_academic_target_baselines():
    assert TARGET_MAPPING_ACCURACY == pytest.approx(0.95)
    assert TARGET_CONCENTRATION_ACCURACY == pytest.approx(0.95)
