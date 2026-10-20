import pytest
from app.engine.deployment_topology_modeling.drift_analysis.placement_drift_diff_engine import (
    PlacementDriftDiffEngine,
)
from app.engine.deployment_topology_modeling.drift_analysis.failure_domain_exposure_engine import (
    FailureDomainExposureEngine,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import ClusterExposureReport


def _exp(name, nodes, ready=True):
    pods = [{"name": f"{name}-{i}", "node": n, "ready": ready} for i, n in enumerate(nodes)]
    return FailureDomainExposureEngine().evaluate_service_exposure(name, pods)


def _report(*services):
    return ClusterExposureReport(timestamp="t", total_services=len(services), evaluated_services=len(services),
                                 exposed_service_count=0, services=list(services))


def test_no_drift_when_status_unchanged():
    diff = PlacementDriftDiffEngine.diff_snapshots(_report(_exp("pay", ["n1", "n2"])), _report(_exp("pay", ["n1", "n2"])))
    assert diff.total_services_compared == 1
    assert diff.drifted_services_count == 0
    assert diff.placement_drift_rate == pytest.approx(0.0)


def test_study_guide_example_and_remediation():
    # Study Guide section 10: payment worker-a/worker-b -> worker-a/worker-a (newly exposed);
    # cart moves from all-on-n1 to n1/n2 (remediated); search unchanged.
    t0 = _report(_exp("payment", ["worker-a", "worker-b"]), _exp("cart", ["n1", "n1"]), _exp("search", ["n1", "n2"]))
    t1 = _report(_exp("payment", ["worker-a", "worker-a"]), _exp("cart", ["n1", "n2"]), _exp("search", ["n1", "n2"]))
    diff = PlacementDriftDiffEngine.diff_snapshots(t0, t1)
    assert diff.total_services_compared == 3
    assert diff.drifted_services == ["cart", "payment"]
    assert diff.placement_drift_rate == pytest.approx(2 / 3, abs=1e-4)
    assert diff.newly_exposed_services == ["payment"]
    assert diff.remediated_services == ["cart"]
    assert diff.state_transitions["payment"] == "NOT_EXPOSED -> EXPOSED"


def test_concentration_change_alone_is_not_drift():
    # 3 replicas 1/1/1 -> 2/1 changes the ratio (1/3 -> 2/3) but not the exposure status.
    t0 = _report(_exp("ui", ["n1", "n2", "n3"]))
    t1 = _report(_exp("ui", ["n1", "n1", "n2"]))
    assert PlacementDriftDiffEngine.diff_snapshots(t0, t1).placement_drift_rate == pytest.approx(0.0)


def test_exposed_node_moving_is_not_a_status_change():
    t0 = _report(_exp("pay", ["n1", "n1"]))
    t1 = _report(_exp("pay", ["n2", "n2"]))
    assert PlacementDriftDiffEngine.diff_snapshots(t0, t1).placement_drift_rate == pytest.approx(0.0)


def test_added_and_removed_services_are_reported_but_not_in_rate():
    t0 = _report(_exp("a", ["n1", "n2"]), _exp("gone", ["n1"]))
    t1 = _report(_exp("a", ["n1", "n1"]), _exp("new", ["n2"]))
    diff = PlacementDriftDiffEngine.diff_snapshots(t0, t1)
    assert diff.total_services_compared == 1
    assert diff.placement_drift_rate == pytest.approx(1.0)
    assert diff.added_services == ["new"]
    assert diff.removed_services == ["gone"]


def test_no_common_services_gives_no_rate():
    diff = PlacementDriftDiffEngine.diff_snapshots(_report(_exp("a", ["n1"])), _report(_exp("b", ["n1"])))
    assert diff.placement_drift_rate is None


def test_accepts_serialised_reports():
    t0 = _report(_exp("a", ["n1", "n2"])).model_dump()
    t1 = _report(_exp("a", ["n1", "n1"])).model_dump()
    assert PlacementDriftDiffEngine.diff_snapshots(t0, t1).placement_drift_rate == pytest.approx(1.0)
