from dataclasses import FrozenInstanceError
import pytest
from app.engine.deployment_topology_modeling.drift_analysis.drift_config import DriftConfig


def test_drift_config_defaults():
    config = DriftConfig()
    assert config.concentration_threshold == pytest.approx(0.50)
    assert config.critical_concentration_threshold == pytest.approx(1.00)
    assert config.default_max_skew == 1
    assert config.failure_domain_key == "kubernetes.io/hostname"
    assert config.allow_scale_divergence is False
    assert config.enforce_resource_limits is True
    assert config.min_recommended_cpu_cores == pytest.approx(0.1)
    assert config.min_recommended_memory_mb == pytest.approx(64.0)
    assert config.treat_single_replica_as_exposed is True
    assert config.enable_audit_logging is True


def test_drift_config_custom_values():
    custom = DriftConfig(
        concentration_threshold=0.60,
        critical_concentration_threshold=0.90,
        default_max_skew=2,
        failure_domain_key="topology.kubernetes.io/zone",
        allow_scale_divergence=True,
        enforce_resource_limits=False,
    )
    assert custom.concentration_threshold == pytest.approx(0.60)
    assert custom.critical_concentration_threshold == pytest.approx(0.90)
    assert custom.default_max_skew == 2
    assert custom.failure_domain_key == "topology.kubernetes.io/zone"
    assert custom.allow_scale_divergence is True
    assert custom.enforce_resource_limits is False


def test_drift_config_immutability():
    config = DriftConfig()
    with pytest.raises(FrozenInstanceError):
        config.concentration_threshold = 0.8  # type: ignore
