"""Combinations of two changes on the live pipeline: Phase 1's evaluator, the capacity ledger and Component 3's
predictor, end to end on a fake cluster. Nothing is applied; a combination is one logical, approvable proposal.

Numerical scenario used throughout: cart (2 replicas) and db (3 replicas) all run on w1, web is spread over w2
and w3, and the workflow needs web, db and cart. Losing w1 takes both cart and db down.
"""

import pytest

from app.engine.recommendation_evaluation_validation.live_pipeline import build_live_recommendation
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph
from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)
from tests.recommendation_evaluation_validation.test_live_ranking import request_live, two_concentrated_services
from tests.recommendation_evaluation_validation.test_live_recommendation import (  # noqa: F401  (fixtures)
    LIVE,
    PARAMS,
    evidenced_topology,
    freeze_checkout,
)
from types import SimpleNamespace

from app.engine.workflow_dependency_modeling import service as workflow_service
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod

COMBO = "rec-checkout-cart+db-combination"
CART = "rec-checkout-cart-topology-spread"
DB = "rec-checkout-db-topology-spread"


def three_concentrated() -> FakeClusterSource:
    return FakeClusterSource(
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod(f"db-{i}", "w1", app="db") for i in range(3)]
                      + [pod(f"cart-{i}", "w1", app="cart") for i in range(2)]
                      + [pod(f"search-{i}", "w1", app="search") for i in range(2)]
                      + [pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"shop": [deployment("db", 3), deployment("cart", 2), deployment("search", 2), deployment("web", 2)]},
    )


def scarce_room() -> FakeClusterSource:
    """The same pods as the numerical scenario, but w2 and w3 each have room for one 100m pod only."""
    return FakeClusterSource(
        nodes=[node("w1"), node("w2", cpu="0.15"), node("w3", cpu="0.15")],
        pods=two_concentrated_services().pods, deployments=two_concentrated_services().deployments)


def freeze(monkeypatch, mandatory=("web", "db", "cart"), optional=()):
    graph = WorkflowGraph(workflow_id="checkout", criticality=0.9, mandatory_services=list(mandatory),
                          optional_services=list(optional), edges=[WorkflowEdge(source="web", target="db")])
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph",
                        lambda workflow_id: SimpleNamespace(graph=graph, freeze_id="f1", assessment_id="a1"))


def live(**kwargs):
    return build_live_recommendation("checkout", "w1", "shop", **kwargs)


def by_id(live_recommendation):
    return {c.id: c for c in live_recommendation.report.recommendations}


# --- the numerical scenario: an improvement neither change achieves alone ---------------------------------------

def test_each_change_alone_improves_nothing_but_the_pair_removes_all_the_risk(monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())

    result = live()

    candidates = by_id(result)
    assert candidates[DB].risk_reduction == 0 and candidates[CART].risk_reduction == 0       # alone: blast radius stays 100
    assert candidates[DB].score == 0.0 and candidates[CART].score == 0.0
    combo = candidates[COMBO]
    assert (combo.type, combo.service, combo.feasible) == ("COMBINATION", "cart+db", True)
    assert combo.risk_reduction == pytest.approx(100.0) and combo.predicted_blast_radius == pytest.approx(0.0)


def test_the_pair_is_scored_by_the_phase_2_formula_and_selected(monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())

    result = live()

    combo = by_id(result)[COMBO]
    # Benefit 1.0 (target and cluster reductions are both 1), evidence coverage 1.0, and 3 of the 5 ready replicas
    # (1 of cart's 2, 2 of db's 3) get a new target node: churn 0.6, so 1 * 1 * (1 - 0.3 * 0.6) = 0.82.
    assert combo.score == pytest.approx(0.82)
    assert combo.confidence == pytest.approx(1.0) and combo.resource_cost == "LOW"
    assert result.report.selected_recommendation_id == COMBO
    entry = result.evidence["ranking"]["candidates"][COMBO]
    assert entry["rank"] == 1 and entry["components"]["placementChurn"] == pytest.approx(0.6)
    assert entry["components"]["targetReduction"] == pytest.approx(1.0) and entry["components"]["clusterReduction"] == pytest.approx(1.0)


def test_the_pair_is_one_logical_proposal_with_both_members_and_one_placement(monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())

    evidence = live().evidence

    entry = evidence["combinations"]["entries"][COMBO]
    assert entry["members"] == [CART, DB] and entry["services"] == ["cart", "db"]
    assert entry["status"] == "FEASIBLE" and entry["excluded"] is None
    placement = evidence["feasibility"][COMBO]["placement"]
    assert set(placement) == {"cart-0", "cart-1", "db-0", "db-1", "db-2"}
    assert len(evidence["feasibility"][COMBO]["podsToRecreate"]) == 3


def test_the_members_remain_listed_on_their_own_and_visible_but_unranked(monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())

    ranking = live().evidence["ranking"]["candidates"]

    assert ranking[DB]["rank"] is None and ranking[DB]["excluded"] == "NO_PREDICTED_IMPROVEMENT"
    assert ranking[CART]["rank"] is None and ranking[CART]["excluded"] == "NO_PREDICTED_IMPROVEMENT"


def test_the_evidence_says_what_was_bounded_and_that_rollout_time_was_not_evaluated(monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())

    combos = live().evidence["combinations"]

    assert combos["maxMembers"] == 2 and combos["considered"] == 1 and combos["evaluated"] == 1
    assert combos["truncated"] is False and combos["skippedPairs"] == []
    assert combos["rolloutTime"]["status"] == "NOT_EVALUATED"
    assert "final state" in combos["scope"] and "nothing is applied" in combos["scope"]


# --- shared capacity -------------------------------------------------------------------------------------------

def test_two_changes_that_need_the_same_node_room_are_not_combined(monkeypatch, use_cluster, evidenced_topology):
    # w2 and w3 hold one 100m pod each. db needs two slots and cart one: each fits alone, the pair needs three.
    freeze(monkeypatch)
    use_cluster(scarce_room())

    result = live()

    assert result.evidence["feasibility"][DB]["status"] == "FEASIBLE" and result.evidence["feasibility"][CART]["status"] == "FEASIBLE"
    entry = result.evidence["combinations"]["entries"][COMBO]
    assert entry["status"] == "INSUFFICIENT_EVIDENCE" and "not performed" in entry["reason"]
    assert any("capacity" in attempt.lower() for attempt in entry["attempts"])
    assert COMBO not in by_id(result) and result.report.selected_recommendation_id is None


# --- bounds ----------------------------------------------------------------------------------------------------

def test_the_evaluation_budget_is_configurable_and_truncation_is_reported(monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch, mandatory=("web", "db", "cart", "search"))
    use_cluster(three_concentrated())

    one = live(combination_budget=1).evidence["combinations"]
    none = live(combination_budget=0).evidence["combinations"]
    everything = live().evidence["combinations"]

    assert (one["considered"], one["evaluated"], one["truncated"]) == (3, 1, True)
    assert one["skippedPairs"] == [["cart", "search"], ["db", "search"]]
    assert (none["evaluated"], none["truncated"], none["entries"]) == (0, True, {})
    assert (everything["evaluated"], everything["truncated"]) == (3, False)


def test_a_negative_budget_is_refused(monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())

    with pytest.raises(ValueError):
        live(combination_budget=-1)


def test_a_third_fix_is_not_searched_for_so_a_pair_that_leaves_one_service_dead_is_not_proposed(
        monkeypatch, use_cluster, evidenced_topology):
    # Three services are down with w1. Fixing any two still leaves the third, so no pair improves anything.
    freeze(monkeypatch, mandatory=("web", "db", "cart", "search"))
    use_cluster(three_concentrated())

    result = live()

    assert not any(c.type == "COMBINATION" for c in result.report.recommendations)
    assert result.report.selected_recommendation_id is None
    entries = result.evidence["combinations"]["entries"]
    assert len(entries) == 3 and all(e["excluded"] == "REDUNDANT_MEMBER" for e in entries.values())


# --- every member must be necessary ------------------------------------------------------------------------------

def test_a_member_that_adds_nothing_is_rejected_and_the_single_change_stays_the_recommendation(
        monkeypatch, use_cluster, evidenced_topology):
    # db alone rescues the workflow. cart is optional here, so adding it to the pair changes nothing.
    freeze(monkeypatch, mandatory=("web", "db"), optional=("cart",))
    use_cluster(two_concentrated_services())

    result = live()

    entry = result.evidence["combinations"]["entries"].get(COMBO)
    assert entry is not None and entry["excluded"] == "REDUNDANT_MEMBER" and entry["redundantMembers"] == ["cart"]
    assert COMBO not in by_id(result)
    assert result.report.selected_recommendation_id == DB


# --- only verified changes are combined ----------------------------------------------------------------------------

def test_changes_that_are_not_verified_feasible_are_never_combined(monkeypatch, use_cluster):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())          # no scheduling evidence: neither change is verifiable

    result = live()

    assert result.evidence["combinations"]["considered"] == 0 and result.evidence["combinations"]["entries"] == {}
    assert not any(c.type == "COMBINATION" for c in result.report.recommendations)


def test_nothing_is_applied_to_the_cluster_by_evaluating_a_combination(client, monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    cluster = two_concentrated_services()
    use_cluster(cluster)

    client.get(LIVE, params=PARAMS)

    assert all(call.startswith(("nodes", "pods", "raw:")) or call == "workload-namespaces" for call in cluster.calls)


def test_the_real_topology_is_never_changed_by_a_combination(monkeypatch, use_cluster, evidenced_topology):
    from app.engine.recommendation_evaluation_validation import live_pipeline

    freeze(monkeypatch)
    use_cluster(two_concentrated_services())
    original, seen = live_pipeline.get_runtime_component_inputs, {}

    def capture(workflow_id, namespace="default"):
        inputs = original(workflow_id, namespace)
        seen["graph"], seen["before"] = inputs.deployment, inputs.deployment.model_dump()
        return inputs

    monkeypatch.setattr(live_pipeline, "get_runtime_component_inputs", capture)

    live()

    assert seen["graph"].model_dump() == seen["before"]


# --- how a pair inherits its members' evidence, cost and audit trail -----------------------------------------------

def test_a_pairs_confidence_is_that_of_its_least_observed_member(monkeypatch, use_cluster, evidenced_topology):
    # cart declares three replicas but only two are seen (coverage 0.6667); db is fully seen. The pair is only as
    # well evidenced as its weakest member, and the score scales with it.
    freeze(monkeypatch)
    cluster = two_concentrated_services()
    cluster.deployments["shop"] = [deployment("db", 3), deployment("cart", 3), deployment("web", 2)]
    use_cluster(cluster)

    combo = by_id(live())[COMBO]

    assert combo.confidence == pytest.approx(0.6667, abs=1e-4)
    assert combo.score == pytest.approx(0.6667 * 0.82, abs=1e-3)


def test_a_pairs_cost_is_its_dearest_members_and_an_unknown_cost_makes_it_unknown():
    from app.engine.recommendation_evaluation_validation.live_pipeline import _highest_cost

    assert _highest_cost(["LOW", "LOW"]) == "LOW"
    assert _highest_cost(["LOW", "HIGH"]) == "HIGH" and _highest_cost(["MEDIUM", "LOW"]) == "MEDIUM"
    assert _highest_cost(["LOW", "UNKNOWN"]) == "UNKNOWN" and _highest_cost(["LOW", "free"]) == "UNKNOWN"


def test_a_pair_whose_member_impact_could_not_be_computed_is_not_proposed(monkeypatch, use_cluster, evidenced_topology):
    # Component 3 fails on the two single changes but works on the pair. Without each member's own impact the pair
    # cannot be shown to need both, so it is described in the evidence and not proposed.
    from app.engine.recommendation_evaluation_validation import live_pipeline

    freeze(monkeypatch)
    use_cluster(two_concentrated_services())
    real, calls = live_pipeline.predict_failure_impact, []

    def flaky(workflow, deployment, failed_node):
        calls.append(failed_node)
        if len(calls) in (2, 3):              # 1: baseline, 2 and 3: the single changes, 4: the pair
            raise ValueError("simulated prediction failure")
        return real(workflow, deployment, failed_node)

    monkeypatch.setattr(live_pipeline, "predict_failure_impact", flaky)

    result = live()

    entry = result.evidence["combinations"]["entries"][COMBO]
    assert entry["status"] == "FEASIBLE" and entry["excluded"] == "MEMBER_IMPACT_UNKNOWN"
    assert COMBO not in by_id(result) and result.report.selected_recommendation_id is None


# --- unknown evidence and inconclusive searches fail closed, end to end -----------------------------------------------

def test_unknown_node_capacity_leaves_nothing_verified_and_nothing_combined(monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(FakeClusterSource(
        nodes=[node("w1"), node("w2", cpu=None), node("w3", cpu=None)],
        pods=two_concentrated_services().pods, deployments=two_concentrated_services().deployments))

    result = live()

    assert all(not c.feasible for c in result.report.recommendations)
    assert all(v["status"] == "INSUFFICIENT_EVIDENCE" for v in result.evidence["feasibility"].values())
    assert result.evidence["combinations"]["considered"] == 0 and result.evidence["combinations"]["entries"] == {}
    assert result.report.selected_recommendation_id is None
    assert any("capacity" in item for v in result.evidence["feasibility"].values() for item in v["missingEvidence"])
