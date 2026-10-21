from app.engine.deployment_topology_modeling.drift_analysis.deployment_only_baseline_engine import (
    DeploymentOnlyBaselineEngine,
)
from app.engine.deployment_topology_modeling.drift_analysis.failure_domain_exposure_engine import (
    FailureDomainExposureEngine,
)


def _exposure(name, placement):
    """placement: list of (node, ready) tuples."""
    pods = [{"name": f"{name}-{i}", "node": node, "ready": ready} for i, (node, ready) in enumerate(placement)]
    return FailureDomainExposureEngine().evaluate_service_exposure(name, pods)


def test_deployment_baseline_b2_lexicographic_ranking():
    # Hand-computed:
    #   payment : 1 ready on node-1          -> EXPOSED,     ratio 1.0, ready 1
    #   cart    : 2 ready on node-1          -> EXPOSED,     ratio 1.0, ready 2
    #   frontend: node-1, node-2, node-3     -> NOT_EXPOSED, ratio 1/3
    #   search  : node-1, node-1, node-2     -> NOT_EXPOSED, ratio 2/3
    #   report  : 0 ready                    -> UNAVAILABLE
    # Order: exposed first (fewer ready replicas first), then not exposed by ratio desc,
    # then unavailable.
    exposures = [
        _exposure("frontend", [("node-1", True), ("node-2", True), ("node-3", True)]),
        _exposure("cart", [("node-1", True), ("node-1", True)]),
        _exposure("report", [("node-2", False)]),
        _exposure("payment", [("node-1", True)]),
        _exposure("search", [("node-1", True), ("node-1", True), ("node-2", True)]),
    ]

    b2 = DeploymentOnlyBaselineEngine.generate_baseline_b2(exposures)

    assert [r["service_name"] for r in b2.service_rankings] == ["payment", "cart", "search", "frontend", "report"]
    assert [r["rank"] for r in b2.service_rankings] == [1, 2, 3, 4, 5]
    assert b2.most_vulnerable_service == "payment"
    assert b2.exposed_services_by_node == {"node-1": ["payment", "cart"]}
    assert b2.most_critical_worker_node == "node-1"
    assert b2.total_single_points_of_failure == 2


def test_most_critical_node_tie_breaks_by_name():
    exposures = [_exposure("a", [("node-2", True)]), _exposure("b", [("node-1", True)])]
    assert DeploymentOnlyBaselineEngine.generate_baseline_b2(exposures).most_critical_worker_node == "node-1"


def test_deployment_baseline_b2_empty():
    b2 = DeploymentOnlyBaselineEngine.generate_baseline_b2([])
    assert b2.most_vulnerable_service is None
    assert b2.most_critical_worker_node is None
    assert b2.total_single_points_of_failure == 0
    assert len(b2.service_rankings) == 0
