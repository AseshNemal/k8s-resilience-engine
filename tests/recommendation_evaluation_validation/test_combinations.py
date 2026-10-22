"""Bounded combinations of two compatible candidate changes (Member 4, Phase 2B): the pure part.

Nothing here calls Component 3. A pair is applied to a virtual copy through the capacity ledger, every scheduling
constraint is re-checked for both services in the final state, and the result is either verified FEASIBLE or
inconclusive. A failed search for a joint placement is never reported INFEASIBLE: sequential application of two
individually planned changes does not search every joint arrangement.
"""

import pytest

from app.engine.recommendation_evaluation_validation.combinations import (
    COMBINATION,
    Member,
    PairOutcome,
    combination_id,
    combine_pair,
    combined_simulation,
    plan_pairs,
    redundant_members,
)
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    FeasibilityStatus,
    SchedulingEvidence,
    check_topology_spread_feasibility,
)
from app.engine.recommendation_evaluation_validation.ranking import ImpactSnapshot
from tests.recommendation_evaluation_validation.test_feasibility_analysis import (
    HOSTNAME,
    make_graph,
    make_intent,
    make_node,
    make_service,
    no_taints,
)

FEASIBLE = FeasibilityStatus.FEASIBLE
INSUFFICIENT = FeasibilityStatus.INSUFFICIENT_EVIDENCE


def member(graph, name, evidence):
    result = check_topology_spread_feasibility(graph, name, evidence)
    assert result.status is FEASIBLE, (name, result.reason, result.missing_evidence)
    return Member(id=f"rec-{name}", service=name, feasibility=result)


def four_nodes(**overrides):
    return [make_node(f"n{i}", **overrides.get(f"n{i}", {})) for i in range(1, 5)]


# --- which pairs are looked at -----------------------------------------------------------------------------

def stub(name):
    return Member(id=f"rec-{name}", service=name, feasibility=None)


def test_pairs_are_the_compatible_ones_in_a_fixed_order_whatever_order_the_members_arrive_in():
    plan = plan_pairs([stub("db"), stub("cart"), stub("auth")], budget=10)

    assert [(a.service, b.service) for a, b in plan.pairs] == [("auth", "cart"), ("auth", "db"), ("cart", "db")]
    assert plan_pairs([stub("auth"), stub("cart"), stub("db")], budget=10) == plan


def test_two_candidates_for_the_same_service_are_not_combined_with_each_other():
    same = Member(id="rec-db-other", service="db", feasibility=None)

    plan = plan_pairs([stub("db"), same, stub("cart")], budget=10)

    assert all(a.service != b.service for a, b in plan.pairs) and plan.considered == 2


def test_the_budget_limits_the_pairs_evaluated_and_what_was_skipped_is_reported():
    plan = plan_pairs([stub(n) for n in "abcd"], budget=2)

    assert plan.considered == 6 and len(plan.pairs) == 2 and plan.truncated is True
    assert [(a.service, b.service) for a, b in plan.pairs] == [("a", "b"), ("a", "c")]
    assert plan.skipped == (("a", "d"), ("b", "c"), ("b", "d"), ("c", "d"))


def test_a_budget_that_covers_everything_is_not_truncated_and_zero_evaluates_nothing():
    assert plan_pairs([stub(n) for n in "abc"], budget=3).truncated is False
    none = plan_pairs([stub(n) for n in "abc"], budget=0)
    assert none.pairs == () and none.truncated is True and len(none.skipped) == 3


def test_a_negative_budget_is_refused_and_fewer_than_two_members_have_no_pairs():
    with pytest.raises(ValueError):
        plan_pairs([stub("a"), stub("b")], budget=-1)
    assert plan_pairs([stub("a")], budget=5).pairs == () and plan_pairs([], budget=5).considered == 0


def test_the_combination_id_names_both_services_in_a_fixed_order():
    assert combination_id("checkout", ["db", "cart"]) == combination_id("checkout", ["cart", "db"])
    assert combination_id("checkout", ["db", "cart"]) == "rec-checkout-cart+db-combination"


# --- applying a pair --------------------------------------------------------------------------------------

def two_concentrated(**node_overrides):
    nodes = four_nodes(**node_overrides)
    return make_graph(nodes, make_service("cart", ("n1", "n1")), make_service("db", ("n1", "n1", "n1"),
                                                                              intent=make_intent(selector={"app": "db"})))


def test_two_changes_that_fit_together_are_verified_in_the_final_state():
    graph = two_concentrated()
    evidence = no_taints(*graph.nodes)
    first, second = member(graph, "cart", evidence), member(graph, "db", evidence)

    outcome = combine_pair(graph, first, second, evidence)

    assert outcome.status is FEASIBLE and outcome.feasibility.feasible
    assert outcome.order == ("cart", "db")
    assert outcome.feasibility.single_node_resilience is True
    final = {s.name: {p.node for p in s.pods} for s in outcome.deployment.services}
    assert len(final["cart"]) >= 2 and len(final["db"]) >= 2
    assert set(outcome.feasibility.placement) == {"cart-0", "cart-1", "db-0", "db-1", "db-2"}
    assert set(outcome.feasibility.pods_to_recreate) == (
        set(first.feasibility.pods_to_recreate) | set(second.feasibility.pods_to_recreate))


def test_the_original_graph_is_never_changed():
    graph = two_concentrated()
    before = graph.model_dump()
    evidence = no_taints(*graph.nodes)

    combine_pair(graph, member(graph, "cart", evidence), member(graph, "db", evidence), evidence)

    assert graph.model_dump() == before


def test_the_final_state_carries_the_capacity_both_changes_left_behind():
    graph = two_concentrated()
    evidence = no_taints(*graph.nodes)
    outcome = combine_pair(graph, member(graph, "cart", evidence), member(graph, "db", evidence), evidence)

    left = {n.name: n.cpu_available for n in outcome.deployment.nodes}

    # Five pods of 500m: three left n1 (or stayed) and the rest were reserved elsewhere; nothing was lost or made up.
    assert sum(left.values()) == pytest.approx(4 * 4.0)
    assert left["n1"] > 4.0 and any(v < 4.0 for k, v in left.items() if k != "n1")


def test_shared_node_capacity_cannot_be_used_by_both_changes():
    # n2 and n3 each have room for one 500m pod. cart needs one slot and db needs two, individually fine,
    # but together they need three and only two exist. The pair must not be reported feasible.
    graph = two_concentrated(n2={"cpu": 0.6}, n3={"cpu": 0.6}, n4={"cpu": 0.0})
    evidence = no_taints(*graph.nodes)
    first, second = member(graph, "cart", evidence), member(graph, "db", evidence)

    outcome = combine_pair(graph, first, second, evidence)

    assert outcome.status is INSUFFICIENT and outcome.feasibility.feasible is False
    assert outcome.deployment is None and outcome.feasibility.placement == {}
    assert any("capacity" in attempt.lower() for attempt in outcome.attempts)


def test_a_failed_joint_search_is_inconclusive_not_infeasible():
    graph = two_concentrated(n2={"cpu": 0.6}, n3={"cpu": 0.6}, n4={"cpu": 0.0})
    evidence = no_taints(*graph.nodes)

    outcome = combine_pair(graph, member(graph, "cart", evidence), member(graph, "db", evidence), evidence)

    assert outcome.status is not FeasibilityStatus.INFEASIBLE
    assert outcome.feasibility.status is INSUFFICIENT and outcome.feasibility.scheduling is INSUFFICIENT
    assert "not searched" in outcome.reason or "not performed" in outcome.reason
    assert all(f.status is not FeasibilityStatus.INFEASIBLE for f in outcome.feasibility.findings)


def anti_pair():
    # a must never share a node with a pod of b.
    anti = {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"labelSelector": {"matchLabels": {"app": "b"}}, "topologyKey": HOSTNAME}]}
    a = make_service("a", ("n1", "n1"), intent=make_intent(selector={"app": "a"}, pod_anti_affinity_configured=True,
                                                          pod_anti_affinity_rules=anti))
    b = make_service("b", ("n1", "n1"), intent=make_intent(selector={"app": "b"}))
    return make_graph(four_nodes(), a, b)


def test_the_second_change_keeps_away_from_pods_that_refuse_it_even_though_it_has_no_rule_of_its_own():
    graph = anti_pair()
    evidence = no_taints(*graph.nodes)

    outcome = combine_pair(graph, member(graph, "a", evidence), member(graph, "b", evidence), evidence)

    assert outcome.status is FEASIBLE
    nodes = {s.name: {p.node for p in s.pods} for s in outcome.deployment.services}
    assert nodes["a"].isdisjoint(nodes["b"]) and len(nodes["a"]) >= 2 and len(nodes["b"]) >= 2


def test_both_services_are_re_checked_in_the_final_state_with_their_placements_held():
    # The analyzer is wrapped, not replaced: every call is recorded and still answered by the real code.
    from app.engine.recommendation_evaluation_validation import combinations

    graph = anti_pair()
    evidence = no_taints(*graph.nodes)
    first, second = member(graph, "a", evidence), member(graph, "b", evidence)
    real, held_checks = combinations.check_topology_spread_feasibility, []

    def spy(deployment, service, *args, **kwargs):
        if kwargs.get("hold_placement"):
            held_checks.append((service, {p.name: p.node for s in deployment.services if s.name == service for p in s.pods}))
        return real(deployment, service, *args, **kwargs)

    combinations.check_topology_spread_feasibility = spy
    try:
        outcome = combine_pair(graph, first, second, evidence)
    finally:
        combinations.check_topology_spread_feasibility = real

    assert outcome.status is FEASIBLE
    chosen = {s.name: {p.name: p.node for p in s.pods} for s in outcome.deployment.services}
    # Both orders are tried, so there are more checks than two; the chosen final state must have had both of its
    # services checked in place, not only the change that was applied second.
    for service in ("a", "b"):
        assert (service, chosen[service]) in held_checks


def test_a_final_state_check_that_fails_rejects_the_pair():
    from app.engine.recommendation_evaluation_validation import combinations

    graph = anti_pair()
    evidence = no_taints(*graph.nodes)
    first, second = member(graph, "a", evidence), member(graph, "b", evidence)
    real = combinations.check_topology_spread_feasibility

    def veto_a(deployment, service, *args, **kwargs):
        result = real(deployment, service, *args, **kwargs)
        if kwargs.get("hold_placement") and service == "a":
            return real(deployment, "missing-service", *args)            # an INSUFFICIENT_EVIDENCE answer
        return result

    combinations.check_topology_spread_feasibility = veto_a
    try:
        outcome = combine_pair(graph, first, second, evidence)
    finally:
        combinations.check_topology_spread_feasibility = real

    assert outcome.status is INSUFFICIENT and outcome.deployment is None
    assert any("final state" in attempt for attempt in outcome.attempts)


def test_of_two_workable_orders_the_one_that_moves_fewer_pods_is_kept():
    graph = two_concentrated()
    evidence = no_taints(*graph.nodes)
    first, second = member(graph, "cart", evidence), member(graph, "db", evidence)

    outcome = combine_pair(graph, first, second, evidence)
    reverse = combine_pair(graph, second, first, evidence)

    assert len(outcome.feasibility.pods_to_recreate) == len(reverse.feasibility.pods_to_recreate)
    assert outcome.order == reverse.order == ("cart", "db")             # the same canonical choice either way


def test_missing_scheduling_evidence_keeps_the_pair_unverified():
    graph = two_concentrated()
    evidence = no_taints(*graph.nodes)
    first, second = member(graph, "cart", evidence), member(graph, "db", evidence)

    outcome = combine_pair(graph, first, second, SchedulingEvidence(node_taints={}))

    assert outcome.status is INSUFFICIENT and outcome.feasibility.feasible is False
    assert any("taints" in item for item in outcome.feasibility.missing_evidence)


def test_unknown_node_capacity_keeps_the_pair_unverified():
    graph = two_concentrated(n2={"cpu": None}, n3={"cpu": None}, n4={"cpu": None})
    evidence = no_taints(*graph.nodes)

    # Each service alone is already unverifiable here, so hand the pair its (stale) individual plans.
    plans = {name: check_topology_spread_feasibility(two_concentrated(), name, no_taints(*graph.nodes))
             for name in ("cart", "db")}
    outcome = combine_pair(graph, Member("rec-cart", "cart", plans["cart"]), Member("rec-db", "db", plans["db"]), evidence)

    assert outcome.status is INSUFFICIENT and outcome.deployment is None


def test_the_combined_result_states_that_only_the_final_state_was_checked():
    graph = two_concentrated()
    evidence = no_taints(*graph.nodes)

    outcome = combine_pair(graph, member(graph, "cart", evidence), member(graph, "db", evidence), evidence)

    assert any("final state" in item and "rollout" in item.lower() for item in outcome.feasibility.assumptions)
    assert any("rollout" in item for item in outcome.feasibility.not_modeled)


def test_a_combined_simulation_is_one_logical_proposal_with_both_services():
    graph = two_concentrated()
    evidence = no_taints(*graph.nodes)
    outcome = combine_pair(graph, member(graph, "cart", evidence), member(graph, "db", evidence), evidence)

    simulation = combined_simulation(graph, outcome)

    assert simulation.recommendation_type == COMBINATION and simulation.service == "cart+db"
    assert simulation.feasible is True and simulation.feasibility_status is FEASIBLE
    assert simulation.pods_to_recreate == tuple(sorted(outcome.feasibility.pods_to_recreate))
    assert simulation.simulated_deployment is outcome.deployment
    assert simulation.concentration_before == pytest.approx(1.0) and simulation.concentration_after < 1.0
    assert simulation.concentration_reduction == pytest.approx(simulation.concentration_before - simulation.concentration_after, abs=0.01)


def test_an_unverified_pair_has_no_simulated_deployment():
    graph = two_concentrated(n2={"cpu": 0.6}, n3={"cpu": 0.6}, n4={"cpu": 0.0})
    evidence = no_taints(*graph.nodes)
    outcome = combine_pair(graph, member(graph, "cart", evidence), member(graph, "db", evidence), evidence)

    simulation = combined_simulation(graph, outcome)

    assert simulation.feasible is False and simulation.simulated_deployment is None and simulation.pods_to_recreate == ()


# --- every member must be necessary -------------------------------------------------------------------------

def snap(assessed, others=(0.0, 0.0, 0.0)):
    return ImpactSnapshot("n1", assessed, 100.0 - assessed, {"n1": assessed} | {f"o{i}": v for i, v in enumerate(others)})


BEFORE = snap(100.0)


def test_a_member_that_adds_nothing_beyond_the_other_one_is_redundant():
    # db alone already removes all the risk; adding cart changes nothing.
    singles = {"db": snap(0.0), "cart": snap(100.0)}

    assert redundant_members(BEFORE, snap(0.0), singles) == ["cart"]


def test_two_members_that_each_matter_are_not_redundant():
    # Neither fixes it alone (no improvement), together they remove all the risk.
    singles = {"db": snap(100.0), "cart": snap(100.0)}

    assert redundant_members(BEFORE, snap(0.0), singles) == []


def test_partial_improvements_that_add_up_are_not_redundant():
    singles = {"db": snap(60.0), "cart": snap(70.0)}

    assert redundant_members(BEFORE, snap(30.0), singles) == []


def test_a_combination_no_better_than_either_member_makes_both_redundant():
    singles = {"db": snap(50.0), "cart": snap(50.0)}

    assert sorted(redundant_members(BEFORE, snap(50.0), singles)) == ["cart", "db"]


def test_a_member_whose_own_impact_is_unknown_cannot_be_shown_necessary():
    assert redundant_members(BEFORE, snap(0.0), {"db": snap(100.0), "cart": None}) is None
    assert redundant_members(BEFORE, snap(0.0), {"db": snap(100.0), "cart": snap(float("nan"))}) is None
    assert redundant_members(BEFORE, None, {"db": snap(100.0), "cart": snap(100.0)}) is None
