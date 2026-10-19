from app.engine.deployment_topology_modeling.drift_analysis.topology_spread_detector import TopologySpreadDriftDetector
from app.engine.deployment_topology_modeling.drift_analysis.constants import DriftType, DriftSeverity


def test_topology_spread_detector_compliant():
    detector = TopologySpreadDriftDetector()
    declared = {
        "topology_spread_constraints": [
            {
                "max_skew": 1,
                "topology_key": "kubernetes.io/hostname",
                "when_unsatisfiable": "DoNotSchedule",
            }
        ]
    }
    runtime_pods = [
        {"name": "api-1", "node_name": "worker-1"},
        {"name": "api-2", "node_name": "worker-2"},
        {"name": "api-3", "node_name": "worker-1"},
    ]
    cluster_nodes = [
        {"name": "worker-1", "labels": {"kubernetes.io/hostname": "worker-1"}},
        {"name": "worker-2", "labels": {"kubernetes.io/hostname": "worker-2"}},
    ]
    violations = detector.detect("api", declared, runtime_pods, cluster_nodes)
    # Skew is 2 - 1 = 1, which equals max_skew 1, so compliant!
    assert len(violations) == 0
    assert detector.is_compliant(violations) is True


def test_topology_spread_detector_skew_exceeded():
    detector = TopologySpreadDriftDetector()
    declared = {
        "topology_spread_constraints": [
            {
                "max_skew": 1,
                "topology_key": "kubernetes.io/hostname",
                "when_unsatisfiable": "DoNotSchedule",
            }
        ]
    }
    runtime_pods = [
        {"name": "api-1", "node_name": "worker-1"},
        {"name": "api-2", "node_name": "worker-1"},
        {"name": "api-3", "node_name": "worker-1"},
        {"name": "api-4", "node_name": "worker-2"},
    ]
    cluster_nodes = [
        {"name": "worker-1", "labels": {"kubernetes.io/hostname": "worker-1"}},
        {"name": "worker-2", "labels": {"kubernetes.io/hostname": "worker-2"}},
    ]
    violations = detector.detect("api", declared, runtime_pods, cluster_nodes)
    # Skew is 3 - 1 = 2 > max_skew 1
    assert len(violations) == 1
    assert violations[0].drift_type == DriftType.TOPOLOGY_SPREAD_DRIFT
    assert violations[0].severity == DriftSeverity.HIGH
    assert violations[0].expected == 1
    assert violations[0].actual == 2
    assert detector.is_compliant(violations) is False


def test_topology_spread_detector_zone_skew():
    detector = TopologySpreadDriftDetector()
    declared = {
        "topology_spread_constraints": [
            {
                "max_skew": 1,
                "topology_key": "topology.kubernetes.io/zone",
                "when_unsatisfiable": "ScheduleAnyway",
            }
        ]
    }
    runtime_pods = [
        {"name": "p-1", "node_name": "node-us-east-1a"},
        {"name": "p-2", "node_name": "node-us-east-1a"},
        {"name": "p-3", "node_name": "node-us-east-1a"},
    ]
    cluster_nodes = [
        {"name": "node-us-east-1a", "labels": {"topology.kubernetes.io/zone": "us-east-1a"}},
        {"name": "node-us-east-1b", "labels": {"topology.kubernetes.io/zone": "us-east-1b"}},
    ]
    violations = detector.detect("svc", declared, runtime_pods, cluster_nodes)
    # 3 in zone a, 0 in zone b -> actual skew 3 > 1
    assert len(violations) == 1
    assert violations[0].severity == DriftSeverity.MEDIUM  # Because ScheduleAnyway
    assert violations[0].actual == 3


# --- Regression tests (audit 2026-10-19) -------------------------------------------------------

from app.engine.deployment_topology_modeling.manifest_parsing.manifest_models import (
    DeclaredIntent,
    TopologyConstraint,
)

HOSTNAME = "kubernetes.io/hostname"


def _nodes(*names, **extra_labels):
    return [{"name": n, "labels": {HOSTNAME: n, **extra_labels.get(n, {})}} for n in names]


def test_reads_constraints_from_real_declared_intent_model():
    # The pipeline passes DeclaredIntent (field `spread_constraints`). Before the fix the detector
    # only read `topology_spread_constraints` and silently found nothing.
    intent = DeclaredIntent(service_name="pay", spread_constraints=[
        TopologyConstraint(max_skew=1, topology_key=HOSTNAME, when_unsatisfiable="DoNotSchedule")
    ])
    pods = [{"name": f"p{i}", "node": "w1"} for i in range(3)]
    violations = TopologySpreadDriftDetector().detect("pay", intent, pods, _nodes("w1", "w2", "w3"))
    assert len(violations) == 1
    assert violations[0].actual == 3  # counts 3/0/0 -> skew 3


def test_skew_only_over_nodes_eligible_by_node_selector():
    # nodeSelector restricts the service to tier=a nodes (w1, w2); w3 must not count as an empty domain.
    intent = {"node_selector": {"tier": "a"},
              "spread_constraints": [{"max_skew": 1, "topology_key": HOSTNAME, "when_unsatisfiable": "DoNotSchedule"}]}
    nodes = _nodes("w1", "w2", "w3", w1={"tier": "a"}, w2={"tier": "a"})
    pods = [{"name": "a", "node": "w1"}, {"name": "b", "node": "w2"}]
    assert TopologySpreadDriftDetector().detect("s", intent, pods, nodes) == []


def test_min_domains_forces_global_minimum_to_zero():
    # 2 eligible domains with 1 pod each: skew 0. With minDomains=3 the global minimum is 0 -> skew 1.
    base = {"max_skew": 0, "topology_key": HOSTNAME, "when_unsatisfiable": "DoNotSchedule"}
    pods = [{"name": "a", "node": "w1"}, {"name": "b", "node": "w2"}]
    nodes = _nodes("w1", "w2")
    assert TopologySpreadDriftDetector().detect("s", {"spread_constraints": [base]}, pods, nodes) == []
    with_min = {**base, "min_domains": 3}
    (v,) = TopologySpreadDriftDetector().detect("s", {"spread_constraints": [with_min]}, pods, nodes)
    assert v.actual == 1


def test_constraint_missing_max_skew_is_not_evaluated():
    intent = {"spread_constraints": [{"topology_key": HOSTNAME}]}
    pods = [{"name": f"p{i}", "node": "w1"} for i in range(3)]
    assert TopologySpreadDriftDetector().detect("s", intent, pods, _nodes("w1", "w2")) == []
