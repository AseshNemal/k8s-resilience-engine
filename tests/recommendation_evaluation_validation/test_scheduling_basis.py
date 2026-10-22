"""The digest of the scheduling constraints and model settings a feasibility verdict was reached under.

It is part of what an approval is bound to. It must move when a constraint that can change the verdict changes,
and stay put when only live cluster state or unrelated workload fields change (those are caught by re-evaluating
the exact placement, and would make every approval brittle if they were in the digest).
"""

import pytest

from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    STRATEGY_MODEL,
    scheduling_basis,
)
from app.shared.schemas.deployment import TopologySpreadConstraintModel
from tests.recommendation_evaluation_validation.test_feasibility_analysis import (
    HOSTNAME,
    make_graph,
    make_intent,
    make_node,
    make_service,
    refuses,
    three_nodes,
)


def basis(graph, services=("db",), **kwargs):
    return scheduling_basis(graph, list(services), **kwargs)


def graph_with(**intent_changes):
    return make_graph(three_nodes(), make_service(intent=make_intent(**intent_changes)))


def test_the_digest_is_stable_and_names_the_strategy_model():
    assert basis(graph_with()) == basis(graph_with()) and len(basis(graph_with())) == 64
    assert "hostname" in STRATEGY_MODEL and "DoNotSchedule" in STRATEGY_MODEL


@pytest.mark.parametrize("change", [
    {"node_selector": {"disk": "ssd"}},
    {"tolerations": [{"key": "dedicated", "operator": "Exists"}]},
    {"node_affinity_configured": True, "node_affinity_rules": {"requiredDuringSchedulingIgnoredDuringExecution": {
        "nodeSelectorTerms": [{"matchExpressions": [{"key": "disk", "operator": "In", "values": ["ssd"]}]}]}}},
    {"pod_anti_affinity_configured": True, "pod_anti_affinity_rules": refuses("db")},
    {"pod_affinity_configured": True, "pod_affinity_rules": refuses("cache")},
    {"topology_spread_constraints": [TopologySpreadConstraintModel(
        max_skew=1, topology_key="topology.kubernetes.io/zone", when_unsatisfiable="DoNotSchedule")]},
    {"resource_requests": [{"cpu": "2", "memory": "1Gi"}]},
    {"selector": {"app": "db", "tier": "data"}},
])
def test_a_change_to_the_workloads_scheduling_constraints_changes_the_digest(change):
    assert basis(graph_with(**change)) != basis(graph_with())


def test_a_changed_spread_constraint_detail_changes_the_digest():
    def with_skew(skew):
        return graph_with(topology_spread_constraints=[TopologySpreadConstraintModel(
            max_skew=skew, topology_key="topology.kubernetes.io/zone", when_unsatisfiable="DoNotSchedule")])

    assert basis(with_skew(1)) != basis(with_skew(2))


def test_the_strategy_setting_is_part_of_the_digest():
    assert basis(graph_with(), max_skew=1) != basis(graph_with(), max_skew=2)


def test_another_services_anti_affinity_that_could_repel_the_moving_pods_is_part_of_the_digest():
    cache = lambda rules: make_service("cache", placement=("n2",), intent=make_intent(    # noqa: E731
        selector={"app": "cache"}, pod_anti_affinity_configured=bool(rules), pod_anti_affinity_rules=rules))
    plain = make_graph(three_nodes(), make_service(), cache({}))
    refusing = make_graph(three_nodes(), make_service(), cache(refuses("db")))

    assert basis(plain) != basis(refusing)


def test_the_digest_covers_every_service_of_a_combination_and_ignores_their_order():
    graph = make_graph(three_nodes(), make_service("cart", ("n1", "n1")), make_service())
    changed = make_graph(three_nodes(), make_service("cart", ("n1", "n1"), intent=make_intent(node_selector={"disk": "ssd"})),
                         make_service())

    assert basis(graph, ("cart", "db")) == basis(graph, ("db", "cart"))
    assert basis(graph, ("cart", "db")) != basis(changed, ("cart", "db"))
    assert basis(graph, ("db",)) == basis(changed, ("db",))        # cart's own constraints are not db's


def test_changing_how_the_proposal_is_modelled_changes_the_digest(monkeypatch):
    from app.engine.recommendation_evaluation_validation import feasibility_analyzer

    before = basis(graph_with())
    monkeypatch.setattr(feasibility_analyzer, "STRATEGY_MODEL", "topology-spread/zone/ScheduleAnyway/v2")

    assert basis(graph_with()) != before


@pytest.mark.parametrize("make", [
    lambda: make_graph([make_node(n.name, cpu=0.1, ready=False) for n in three_nodes()], make_service()),   # node state
    lambda: make_graph(three_nodes(), make_service(placement=("n2", "n3", "n3"))),                         # where the pods are
    lambda: make_graph(three_nodes(), make_service(placement=("n1",) * 5)),                                # how many pods
    lambda: make_graph(three_nodes(), make_service(intent=make_intent(declared_replicas=9))),              # replica count
    lambda: make_graph(three_nodes(), make_service(intent=make_intent(readiness_probe_configured=True))),  # not scheduling
])
def test_live_state_and_unrelated_fields_do_not_change_the_digest(make):
    assert basis(make()) == basis(graph_with())


def test_a_service_without_declared_intent_has_its_own_digest():
    assert basis(make_graph(three_nodes(), make_service(intent=None))) != basis(graph_with())
    assert HOSTNAME  # the helper module is the one the other feasibility tests use
