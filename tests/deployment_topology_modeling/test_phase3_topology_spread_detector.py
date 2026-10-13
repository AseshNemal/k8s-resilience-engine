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
