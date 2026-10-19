"""Hand-computed tests for the single definitions in placement_metrics (Study Guide sections 9-12)."""
import pytest
from app.engine.deployment_topology_modeling import placement_metrics as pm
from app.engine.deployment_topology_modeling.placement_metrics import ExposureStatus as S


def pods(*placement):
    """placement items: node name (ready) or (node, ready)."""
    out = []
    for i, item in enumerate(placement):
        node, ready = item if isinstance(item, tuple) else (item, True)
        out.append({"name": f"p{i}", "node": node, "ready": ready})
    return out


def test_study_guide_section_9_table():
    # Payment: worker-a, worker-a -> fully exposed to worker-a
    assert pm.exposed_failure_domains(pods("worker-a", "worker-a")) == ["worker-a"]
    # Order: worker-a, worker-b -> not fully exposed
    assert pm.exposed_failure_domains(pods("worker-a", "worker-b")) == []
    # Inventory: 1 replica on worker-c -> exposed due to single replica
    assert pm.exposed_failure_domains(pods("worker-c")) == ["worker-c"]


def test_study_guide_section_12_exposure_rate_example():
    # 10 services evaluated, 3 can lose all ready replicas -> 0.30
    statuses = [S.EXPOSED] * 3 + [S.NOT_EXPOSED] * 7
    assert pm.exposure_rate(statuses) == pytest.approx(0.30)


def test_concentration_definitions():
    assert pm.is_concentrated(pods("n1", "n1", "n1")) is True       # TAF: all ready on one domain
    assert pm.is_concentrated(pods("n1", "n1", "n2")) is False
    assert pm.concentration_ratio(pods("n1", "n1", "n2")) == pytest.approx(2 / 3)
    assert pm.concentration_ratio(pods("n1", "n2", "n3", "n4")) == pytest.approx(0.25)
    assert pm.is_concentrated([]) is None
    assert pm.concentration_ratio([]) is None


def test_only_ready_bound_pods_are_replicas():
    mixed = pods(("n1", True), ("n2", False), (None, True))
    assert pm.ready_replica_count(mixed) == 1
    assert pm.ready_replicas_per_node(mixed) == {"n1": 1}
    assert pm.exposure_status(mixed) == S.EXPOSED


def test_exposure_status_values():
    assert pm.exposure_status(pods("n1", "n2")) == S.NOT_EXPOSED
    assert pm.exposure_status(pods("n1", "n1")) == S.EXPOSED
    assert pm.exposure_status(pods(("n1", False))) == S.UNAVAILABLE
    assert pm.exposure_status([]) == S.UNAVAILABLE


def test_exposure_rate_excludes_unavailable_and_handles_empty():
    assert pm.exposure_rate([S.EXPOSED, S.NOT_EXPOSED, S.UNAVAILABLE]) == pytest.approx(0.5)
    assert pm.exposure_rate([S.UNAVAILABLE]) is None
    assert pm.exposure_rate([]) is None


def test_compliance_rate_only_over_services_with_rules():
    assert pm.compliance_rate({"a": True, "b": False, "c": None, "d": True}) == pytest.approx(2 / 3)
    assert pm.compliance_rate({"a": None}) is None
    assert pm.compliance_rate({}) is None


def test_placement_drift_rate():
    before = {"pay": S.NOT_EXPOSED, "cart": S.EXPOSED, "ui": S.NOT_EXPOSED, "old": S.EXPOSED}
    after = {"pay": S.EXPOSED, "cart": S.EXPOSED, "ui": S.UNAVAILABLE, "new": S.EXPOSED}
    drift = pm.placement_drift(before, after)
    assert drift.compared_services == ["cart", "pay", "ui"]
    assert drift.changed_services == ["pay", "ui"]
    assert drift.rate == pytest.approx(2 / 3)
    assert drift.added_services == ["new"]
    assert drift.removed_services == ["old"]
    assert drift.transitions["pay"] == "NOT_EXPOSED -> EXPOSED"
    assert pm.placement_drift({}, {}).rate is None
