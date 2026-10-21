"""Cluster-level rates from DriftAnalyzer (Study Guide section 12), hand-computed."""
import pytest
from app.engine.deployment_topology_modeling.drift_analysis.drift_analyzer import DriftAnalyzer

HOST = "kubernetes.io/hostname"
NODES = [{"name": n, "labels": {HOST: n}} for n in ("n1", "n2", "n3")]


def _pods(*nodes, ready=True):
    return [{"name": f"p{i}", "node": n, "ready": ready} for i, n in enumerate(nodes)]


def test_missing_resource_limits_do_not_break_compliance():
    # Before the fix: a well-spread service without limits gave compliance 0.0.
    manifests = {"pay": {"expected_replicas": 2, "resources": [],
                         "spread_constraints": [{"max_skew": 1, "topology_key": HOST, "when_unsatisfiable": "DoNotSchedule"}]}}
    report, _ = DriftAnalyzer().analyze_cluster(manifests, {"pay": _pods("n1", "n2")}, NODES)
    assert report.constraint_compliance_rate == pytest.approx(1.0)
    kinds = {v.drift_type.value for v in report.services[0].violations}
    assert kinds == {"RESOURCE_CONTENTION_DRIFT"}  # still reported, as information


def test_rates_hand_computed():
    # pay  : spread rule, 3 pods on n1 -> violates spread, EXPOSED
    # cart : spread rule, n1/n2        -> complies, NOT_EXPOSED
    # ui   : no rules, n1              -> excluded from compliance, EXPOSED
    # dead : no ready replica          -> UNAVAILABLE, excluded from exposure rate
    spread = {"spread_constraints": [{"max_skew": 1, "topology_key": HOST, "when_unsatisfiable": "DoNotSchedule"}]}
    manifests = {"pay": spread, "cart": spread}
    pods = {"pay": _pods("n1", "n1", "n1"), "cart": _pods("n1", "n2"), "ui": _pods("n1"),
            "dead": _pods("n2", ready=False)}
    report, b2 = DriftAnalyzer().analyze_cluster(manifests, pods, NODES)
    assert report.total_services == 4
    assert report.evaluated_services == 3
    assert report.unavailable_services == ["dead"]
    assert report.exposed_services == ["pay", "ui"]
    assert report.exposure_rate == pytest.approx(2 / 3, abs=1e-4)
    assert report.services_with_placement_rules == 2
    assert report.constraint_compliance_rate == pytest.approx(0.5)
    # mean ratio over available services: pay 1.0, cart 0.5, ui 1.0
    assert report.mean_concentration == pytest.approx(2.5 / 3, abs=1e-4)
    assert b2.most_critical_worker_node == "n1"


def test_empty_cluster_rates_are_not_invented():
    report, b2 = DriftAnalyzer().analyze_cluster({}, {}, NODES)
    assert report.exposure_rate is None
    assert report.constraint_compliance_rate is None
    assert report.mean_concentration is None
    assert b2.service_rankings == []


def test_manifest_without_running_service_is_not_evaluated():
    report, _ = DriftAnalyzer().analyze_cluster({"ghost": {"expected_replicas": 2}}, {}, NODES)
    assert report.total_services == 0
