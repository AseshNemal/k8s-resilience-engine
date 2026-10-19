from app.engine.deployment_topology_modeling.drift_analysis.affinity_drift_detector import AffinityDriftDetector
from app.engine.deployment_topology_modeling.drift_analysis.constants import DriftType, DriftSeverity


def test_affinity_drift_detector_compliant():
    detector = AffinityDriftDetector()
    declared = {
        "affinity": {
            "podAntiAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": [
                    {"labelSelector": {"matchLabels": {"app": "auth"}}}
                ]
            }
        }
    }
    runtime_pods = [
        {"name": "auth-1", "node_name": "worker-1"},
        {"name": "auth-2", "node_name": "worker-2"},
    ]
    violations = detector.detect("auth", declared, runtime_pods, [])
    assert len(violations) == 0
    assert detector.is_compliant(violations) is True


def test_affinity_drift_detector_required_violation():
    detector = AffinityDriftDetector()
    declared = {
        "affinity": {
            "podAntiAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": [
                    {"labelSelector": {"matchLabels": {"app": "payment"}}}
                ]
            }
        }
    }
    runtime_pods = [
        {"name": "payment-1", "node_name": "worker-1"},
        {"name": "payment-2", "node_name": "worker-1"},
    ]
    violations = detector.detect("payment", declared, runtime_pods, [])
    assert len(violations) == 1
    assert violations[0].drift_type == DriftType.AFFINITY_DRIFT
    assert violations[0].severity == DriftSeverity.CRITICAL
    assert violations[0].affected_failure_domain == "worker-1"
    assert violations[0].actual == 2
    assert detector.is_compliant(violations) is False


def test_affinity_drift_detector_preferred_violation():
    detector = AffinityDriftDetector()
    declared = {
        "affinity": {
            "podAntiAffinity": {
                "preferredDuringSchedulingIgnoredDuringExecution": [
                    {"weight": 100}
                ]
            }
        }
    }
    runtime_pods = [
        {"name": "checkout-1", "node_name": "worker-2"},
        {"name": "checkout-2", "node_name": "worker-2"},
    ]
    violations = detector.detect("checkout", declared, runtime_pods, [])
    assert len(violations) == 1
    assert violations[0].severity == DriftSeverity.MEDIUM
    assert violations[0].affected_failure_domain == "worker-2"


def test_anti_affinity_uses_the_rules_topology_key():
    # Zone-level anti-affinity: two pods on different nodes but in the SAME zone violate it,
    # and the same placement does not violate a hostname-level rule.
    detector = AffinityDriftDetector()
    nodes = [
        {"name": "w1", "labels": {"topology.kubernetes.io/zone": "a"}},
        {"name": "w2", "labels": {"topology.kubernetes.io/zone": "a"}},
    ]
    pods = [{"name": "p1", "node_name": "w1"}, {"name": "p2", "node_name": "w2"}]
    zone_rule = {"affinity": {"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"topologyKey": "topology.kubernetes.io/zone"}]}}}
    host_rule = {"affinity": {"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"topologyKey": "kubernetes.io/hostname"}]}}}
    (v,) = detector.detect("svc", zone_rule, pods, nodes)
    assert v.affected_failure_domain == "a" and v.actual == 2
    assert detector.detect("svc", host_rule, pods, nodes) == []


def test_node_selector_violation_from_real_manifest_fields():
    detector = AffinityDriftDetector()
    nodes = [{"name": "w1", "labels": {"research-node": "worker-1"}},
             {"name": "w2", "labels": {"research-node": "worker-2"}}]
    intent = {"node_selector": {"research-node": "worker-1"}}
    assert detector.detect("pay", intent, [{"name": "a", "node": "w1"}], nodes) == []
    (v,) = detector.detect("pay", intent, [{"name": "b", "node": "w2"}], nodes)
    assert v.severity == DriftSeverity.CRITICAL
