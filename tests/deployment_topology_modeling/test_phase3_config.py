import dataclasses

import pytest
from app.engine.deployment_topology_modeling.drift_analysis.drift_config import DriftConfig


def test_drift_config_defaults():
    config = DriftConfig()
    assert config.failure_domain_key == "kubernetes.io/hostname"
    assert config.enforce_resource_limits is True
    assert config.enable_audit_logging is True


def test_drift_config_custom_values():
    custom = DriftConfig(failure_domain_key="topology.kubernetes.io/zone", enforce_resource_limits=False)
    assert custom.failure_domain_key == "topology.kubernetes.io/zone"
    assert custom.enforce_resource_limits is False


def test_drift_config_immutability():
    config = DriftConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.enforce_resource_limits = False  # type: ignore


def test_disabling_resource_limits_removes_those_findings():
    from app.engine.deployment_topology_modeling.drift_analysis.resource_contention_detector import (
        ResourceContentionDetector,
    )
    declared = {"resources": []}
    assert ResourceContentionDetector(DriftConfig()).detect("s", declared, [], [])
    assert ResourceContentionDetector(DriftConfig(enforce_resource_limits=False)).detect("s", declared, [], []) == []
