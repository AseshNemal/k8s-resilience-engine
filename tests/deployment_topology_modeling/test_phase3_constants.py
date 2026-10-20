import pytest
from app.engine.deployment_topology_modeling.drift_analysis import constants
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
    FailureDomainType,
    ExposureLevel,
    PLACEMENT_RULE_DRIFT_TYPES,
    INTENT_DRIFT_TYPES,
    TARGET_MAPPING_ACCURACY,
    TARGET_CONCENTRATION_ACCURACY,
)


def test_drift_type_enum_members():
    assert DriftType.SCALE_DRIFT.value == "SCALE_DRIFT"
    assert DriftType.AFFINITY_DRIFT.value == "AFFINITY_DRIFT"
    assert DriftType.TOPOLOGY_SPREAD_DRIFT.value == "TOPOLOGY_SPREAD_DRIFT"
    assert DriftType.RESOURCE_CONTENTION_DRIFT.value == "RESOURCE_CONTENTION_DRIFT"
    assert len(DriftType) == 4


def test_compliance_counts_only_placement_rules():
    # Study Guide section 12: compliance is about "intended topology or affinity rules".
    assert PLACEMENT_RULE_DRIFT_TYPES == {DriftType.AFFINITY_DRIFT, DriftType.TOPOLOGY_SPREAD_DRIFT}
    assert DriftType.RESOURCE_CONTENTION_DRIFT not in INTENT_DRIFT_TYPES
    assert DriftType.SCALE_DRIFT in INTENT_DRIFT_TYPES


def test_drift_severity_tiers():
    assert [s.value for s in DriftSeverity] == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def test_failure_domain_keys():
    assert FailureDomainType.HOSTNAME.value == "kubernetes.io/hostname"
    assert FailureDomainType.ZONE.value == "topology.kubernetes.io/zone"
    assert FailureDomainType.REGION.value == "topology.kubernetes.io/region"


def test_exposure_levels_have_no_undefined_partial_level():
    assert {e.value for e in ExposureLevel} == {
        "FULL_EXPOSURE", "SINGLE_REPLICA_EXPOSURE", "RESILIENT", "UNAVAILABLE",
    }


def test_no_arbitrary_severity_weights_remain():
    # Proposal sections 7.6 and 12.2 argue against arbitrary weighted scores.
    assert not [name for name in dir(constants) if name.startswith("SEVERITY_WEIGHT")]


def test_self_chosen_targets_match_validation_panel():
    from app.engine.deployment_topology_modeling.web.academic_metric_presenter import AcademicMetricPresenter
    assert TARGET_MAPPING_ACCURACY == pytest.approx(AcademicMetricPresenter.TARGET_MAPPING_ACCURACY)
    assert TARGET_CONCENTRATION_ACCURACY == pytest.approx(AcademicMetricPresenter.TARGET_CONCENTRATION_ACCURACY)
