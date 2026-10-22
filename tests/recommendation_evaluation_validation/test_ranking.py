"""Ranking of evaluated recommendation candidates (Member 4, Phase 2): pure logic, no cluster, no Member 3 code.

Impact snapshots are built by hand here. The formula under test is

    score = confidence * benefit * (1 - d * placement_churn) * (1 - r * resource_fraction)
    benefit = alpha * target_reduction + (1 - alpha) * cluster_reduction

with every term a proportion in [0, 1], so a candidate's score never depends on the other candidates.
"""

import inspect
import itertools
import math
import random

import pytest

from app.engine.recommendation_evaluation_validation import ranking
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import FeasibilityStatus
from app.engine.recommendation_evaluation_validation.ranking import (
    EvaluatedCandidate,
    ExclusionReason,
    ImpactSnapshot,
    RankingWeights,
    rank_candidates,
)
from app.engine.recommendation_evaluation_validation.simulation_result import SimulationResult

FEASIBLE = FeasibilityStatus.FEASIBLE
INFEASIBLE = FeasibilityStatus.INFEASIBLE
INSUFFICIENT = FeasibilityStatus.INSUFFICIENT_EVIDENCE
EXCLUDED = ExclusionReason


def snap(assessed=100.0, others=(0.0, 0.0, 0.0), *, failed="n1", survivability=None, basis="single-workflow"):
    nodes = {failed: assessed} | {f"o{i}": value for i, value in enumerate(others)}
    return ImpactSnapshot(failed_node=failed, blast_radius=assessed,
                          survivability=100.0 - assessed if survivability is None else survivability,
                          node_blast=nodes, basis=basis)


def simulation(status=FEASIBLE, churn=0, service="db"):
    return SimulationResult(
        recommendation_type="TOPOLOGY_SPREAD", service=service, feasible=status is FEASIBLE,
        feasibility_reason="", concentration_before=1.0, concentration_after=0.0, concentration_reduction=1.0,
        simulated_deployment=None, feasibility_status=status,
        pods_to_recreate=tuple(f"{service}-{i}" for i in range(churn)))


FIXED = object()


def cand(name="db", *, before=FIXED, after=FIXED, status=FEASIBLE, churn=0, replicas=3, confidence=1.0,
         cost="LOW", service=None):
    return EvaluatedCandidate(
        id=name, type="TOPOLOGY_SPREAD", service=service or name,
        simulation=simulation(status, churn, service or name),
        before=snap() if before is FIXED else before,
        after=snap(0.0, (0.0, 0.0, 0.0)) if after is FIXED else after,
        ready_replicas=replicas, confidence=confidence, resource_cost=cost)


def only(*candidates, **kwargs):
    (result,) = rank_candidates(list(candidates), **kwargs)
    return result


# --- the formula -----------------------------------------------------------------------------------

def test_a_candidate_that_removes_all_risk_without_churn_scores_one():
    result = only(cand())

    assert result.rank == 1 and result.excluded is None
    assert result.score == pytest.approx(1.0)


def test_the_documented_example_scores_as_computed_by_hand():
    # Blast radius 100 -> 66.67 at the assessed node and mean 25 -> 16.67 over four nodes: both reductions are
    # one third. Two of three ready replicas change node (churn 2/3), so the churn factor is 1 - 0.3 * 2/3 = 0.8.
    result = only(cand(after=snap(66.67, (0.0, 0.0, 0.0)), churn=2, replicas=3))

    assert result.components.target_reduction == pytest.approx(1 / 3, abs=1e-3)
    assert result.components.cluster_reduction == pytest.approx(1 / 3, abs=1e-3)
    assert result.components.benefit == pytest.approx(1 / 3, abs=1e-3)
    assert result.components.placement_churn == pytest.approx(2 / 3)
    assert result.components.churn_factor == pytest.approx(0.8)
    assert result.score == pytest.approx(0.2667, abs=1e-3)


def test_confidence_is_evidence_coverage_and_scales_the_benefit():
    assert only(cand(confidence=0.5)).score == pytest.approx(0.5)
    assert only(cand(confidence=0.5, churn=3)).score == pytest.approx(0.5 * 0.7)  # cost is not discounted by it


def test_the_target_share_weights_assessed_node_against_cluster_wide_reduction():
    # The assessed node is fully protected, but another node became riskier: cluster-wide the gain is smaller.
    before, after = snap(100.0, (50.0, 0.0, 0.0)), snap(0.0, (80.0, 0.0, 0.0))
    only_target = only(cand(before=before, after=after), weights=RankingWeights(target_share=1.0))
    only_cluster = only(cand(before=before, after=after), weights=RankingWeights(target_share=0.0))

    assert only_target.score == pytest.approx(1.0)
    assert only_cluster.score == pytest.approx((150 / 4 - 80 / 4) / (150 / 4))   # mean 37.5 -> 20


def test_placement_churn_discounts_up_to_d_and_resource_cost_up_to_r():
    assert only(cand(churn=3, replicas=3)).score == pytest.approx(0.7)
    assert only(cand(cost="MEDIUM")).score == pytest.approx(1 - 0.3 * 0.5)
    assert only(cand(cost="HIGH")).score == pytest.approx(0.7)
    assert only(cand(churn=3, cost="HIGH")).score == pytest.approx(0.7 * 0.7)


def test_the_default_weights_are_the_experimental_provisional_ones():
    weights = RankingWeights()

    assert (weights.target_share, weights.disruption_discount, weights.resource_discount) == (0.7, 0.3, 0.3)
    assert "experimental" in ranking.WEIGHTS_STATUS and "not" in ranking.WEIGHTS_STATUS


def test_a_larger_benefit_can_justify_more_churn_and_the_weight_decides_where():
    big_but_disruptive = cand("A", after=snap(40.0, (0.0, 0.0, 0.0)), churn=3, replicas=3)       # t = 0.6
    small_and_quiet = cand("B", after=snap(55.0, (0.0, 0.0, 0.0)), churn=0, replicas=3)          # t = 0.45

    def winner(d):
        return rank_candidates([big_but_disruptive, small_and_quiet],
                               RankingWeights(disruption_discount=d))[0].id

    assert winner(0.2) == "A" and winner(0.3) == "B"      # break-even at 1 / (1 - d) = 1.33 .. 1.43


def test_survivability_is_display_only_and_never_scored():
    plain = only(cand(before=snap(100.0, survivability=0.0), after=snap(0.0, survivability=100.0)))
    odd = only(cand(before=snap(100.0, survivability=7.0), after=snap(0.0, survivability=3.0)))

    assert plain.score == odd.score
    assert odd.components.survivability_before == 7.0 and odd.components.survivability_after == 3.0


# --- eligibility: only verified, evidenced, genuinely improving candidates rank -----------------------

@pytest.mark.parametrize("status, reason", [(INFEASIBLE, EXCLUDED.INFEASIBLE),
                                            (INSUFFICIENT, EXCLUDED.INSUFFICIENT_EVIDENCE)])
def test_candidates_that_are_not_verified_feasible_stay_visible_but_unranked(status, reason):
    ranked = rank_candidates([cand("good"), cand("bad", status=status, after=None)])

    assert [r.id for r in ranked] == ["good", "bad"]
    good, bad = ranked
    assert good.rank == 1
    assert bad.rank is None and bad.score is None and bad.excluded is reason and bad.detail


def test_every_candidate_appears_exactly_once_in_the_output():
    inputs = [cand("a"), cand("b", status=INFEASIBLE, after=None), cand("c", after=snap(100.0)), cand("d", cost="?")]

    ranked = rank_candidates(inputs)

    assert sorted(r.id for r in ranked) == ["a", "b", "c", "d"]


def test_a_feasible_candidate_without_member_3_impact_evidence_is_excluded():
    assert only(cand(after=None)).excluded is EXCLUDED.MISSING_IMPACT_EVIDENCE
    assert only(cand(before=None)).excluded is EXCLUDED.MISSING_IMPACT_EVIDENCE


@pytest.mark.parametrize("cost", ["UNKNOWN", "", "FREE", None, "low"])
def test_an_unknown_resource_cost_excludes_the_candidate_rather_than_guessing_a_penalty(cost):
    result = only(cand(cost=cost))

    assert result.excluded is EXCLUDED.UNKNOWN_RESOURCE_COST
    assert result.rank is None and result.score is None


def test_a_zero_baseline_blast_radius_has_nothing_to_improve_and_does_not_divide_by_zero():
    result = only(cand(before=snap(0.0, (0.0, 0.0, 0.0)), after=snap(0.0, (0.0, 0.0, 0.0))))

    assert result.excluded is EXCLUDED.NO_BASELINE_RISK


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -1.0, 100.5])
def test_non_finite_or_out_of_range_impact_values_are_rejected(bad):
    assert only(cand(after=snap(bad))).excluded is EXCLUDED.INVALID_IMPACT_DATA
    assert only(cand(before=snap(100.0, (bad, 0.0, 0.0)))).excluded is EXCLUDED.INVALID_IMPACT_DATA
    assert only(cand(after=snap(0.0, survivability=bad))).excluded is EXCLUDED.INVALID_IMPACT_DATA


@pytest.mark.parametrize("bad", [math.nan, math.inf, -0.1, 1.1, None])
def test_an_invalid_confidence_is_rejected(bad):
    assert only(cand(confidence=bad)).excluded is EXCLUDED.INVALID_INPUT


@pytest.mark.parametrize("replicas", [0, -1, None, math.nan, math.inf])
def test_an_invalid_replica_count_is_rejected(replicas):
    assert only(cand(churn=1, replicas=replicas)).excluded is EXCLUDED.INVALID_INPUT


def test_inconsistent_snapshots_are_rejected():
    mismatched_nodes = snap(0.0, (0.0, 0.0))
    other_node = ImpactSnapshot("n9", 0.0, 100.0, {"n9": 0.0, "o0": 0.0, "o1": 0.0, "o2": 0.0})
    off_basis = snap(0.0, basis="portfolio-equal-weight")
    inconsistent = ImpactSnapshot("n1", 0.0, 100.0, {"n1": 40.0, "o0": 0.0, "o1": 0.0, "o2": 0.0})
    empty = ImpactSnapshot("n1", 0.0, 100.0, {})

    for after in (mismatched_nodes, other_node, off_basis, inconsistent, empty):
        assert only(cand(after=after)).excluded is EXCLUDED.INVALID_IMPACT_DATA


def test_a_candidate_that_makes_the_assessed_node_worse_is_excluded():
    result = only(cand(before=snap(50.0, (0.0, 0.0, 0.0)), after=snap(100.0, (0.0, 0.0, 0.0))))

    assert result.excluded is EXCLUDED.WORSENS_ASSESSED_NODE


def test_a_candidate_that_makes_the_worst_case_node_worse_is_excluded():
    # The assessed node is rescued, but another node ends up riskier than anything was before.
    result = only(cand(before=snap(50.0, (20.0, 0.0, 0.0)), after=snap(0.0, (80.0, 0.0, 0.0))))

    assert result.excluded is EXCLUDED.WORSENS_WORST_CASE_NODE


def test_pure_risk_transfer_between_nodes_is_not_selected():
    # All the risk moved from n1 to o0: the assessed node looks perfect, aggregate and worst case did not move.
    result = only(cand(before=snap(100.0, (0.0, 0.0, 0.0)), after=snap(0.0, (100.0, 0.0, 0.0))))

    assert result.excluded is EXCLUDED.RISK_TRANSFER_ONLY
    assert result.rank is None and result.score is None
    assert result.components.target_reduction == pytest.approx(1.0)    # the evidence stays visible


def test_spreading_risk_lowers_the_worst_case_and_is_not_a_pure_transfer():
    result = only(cand(before=snap(100.0, (0.0, 0.0, 0.0)), after=snap(50.0, (50.0, 0.0, 0.0))))

    assert result.excluded is None
    assert result.components.cluster_reduction == pytest.approx(0.0)
    assert result.components.worst_case_reduction == pytest.approx(0.5)
    assert result.score == pytest.approx(0.7 * 0.5)


def test_no_change_at_all_is_no_predicted_improvement():
    result = only(cand(after=snap(100.0, (0.0, 0.0, 0.0))))

    assert result.excluded is EXCLUDED.NO_PREDICTED_IMPROVEMENT


# --- comparing candidates ----------------------------------------------------------------------------

def test_candidates_assessed_against_different_failures_or_bases_cannot_be_ranked_together():
    with pytest.raises(ValueError, match="same failure"):
        rank_candidates([cand("a"), cand("b", before=snap(100.0, failed="n2"), after=snap(0.0, failed="n2"))])
    with pytest.raises(ValueError, match="same basis"):
        rank_candidates([cand("a"), cand("b", before=snap(basis="portfolio-equal-weight"),
                                         after=snap(0.0, basis="portfolio-equal-weight"))])


def test_ranking_order_is_by_score_then_less_churn_then_cheaper_then_name():
    equal = dict(after=snap(50.0, (0.0, 0.0, 0.0)))
    ranked = rank_candidates([
        cand("zz", **equal, churn=1), cand("aa", **equal, churn=1, cost="MEDIUM"),
        cand("bb", **equal, churn=0), cand("cc", **equal, churn=1),
        cand("top", after=snap(0.0, (0.0, 0.0, 0.0)), churn=0)])

    assert [r.id for r in ranked] == ["top", "bb", "cc", "zz", "aa"]
    assert [r.rank for r in ranked] == [1, 2, 3, 4, 5]


def test_when_churn_and_cost_do_not_change_the_score_they_still_break_ties_before_the_name():
    # With both discounts at zero the scores are equal, so only the documented tie-breaks can order them.
    equal = dict(after=snap(50.0, (0.0, 0.0, 0.0)))
    no_discount = RankingWeights(disruption_discount=0.0, resource_discount=0.0)

    by_churn = rank_candidates([cand("a-busy", **equal, churn=2), cand("z-quiet", **equal, churn=0)], no_discount)
    by_cost = rank_candidates([cand("a-pricey", **equal, cost="HIGH"), cand("z-cheap", **equal, cost="LOW")],
                              no_discount)

    assert by_churn[0].score == by_churn[1].score and [r.id for r in by_churn] == ["z-quiet", "a-busy"]
    assert by_cost[0].score == by_cost[1].score and [r.id for r in by_cost] == ["z-cheap", "a-pricey"]


def test_the_result_does_not_depend_on_the_order_the_candidates_arrive_in():
    items = [cand(f"c{i}", after=snap(100.0 - 20 * i, (0.0, 0.0, 0.0)), churn=i % 3, replicas=3) for i in range(5)]
    items += [cand("x", status=INFEASIBLE, after=None), cand("y", cost="?")]
    expected = rank_candidates(items)

    for seed in range(5):
        shuffled = items[:]
        random.Random(seed).shuffle(shuffled)
        assert rank_candidates(shuffled) == expected


def test_adding_or_removing_another_candidate_never_changes_a_score_or_the_relative_order():
    items = [cand(f"c{i}", after=snap(90.0 - 15 * i, (0.0, 0.0, 0.0)), churn=i % 3) for i in range(6)]
    full = {r.id: r for r in rank_candidates(items)}

    for dropped in items:
        rest = [c for c in items if c.id != dropped.id]
        partial = rank_candidates(rest)
        assert all(r.score == full[r.id].score for r in partial)
        assert [r.id for r in partial] == [i for i in full if i != dropped.id]


def test_a_dominating_candidate_never_ranks_below_the_one_it_dominates_for_any_weights():
    rng = random.Random(3)
    for _ in range(300):
        weak_after = rng.uniform(1.0, 99.0)
        strong_after = rng.uniform(0.0, weak_after)
        weak_churn = rng.randint(0, 3)
        weak = cand("weak", after=snap(weak_after, (0.0, 0.0, 0.0)), churn=weak_churn, confidence=rng.uniform(0.2, 1.0))
        strong = cand("strong", after=snap(strong_after, (0.0, 0.0, 0.0)), churn=rng.randint(0, weak_churn),
                      confidence=weak.confidence)
        weights = RankingWeights(*(rng.uniform(0.0, 1.0) for _ in range(3)))

        assert [r.id for r in rank_candidates([weak, strong], weights)][0] == "strong"


def test_score_is_monotone_in_every_input_and_stays_within_zero_and_one():
    base = dict(after=snap(40.0, (0.0, 0.0, 0.0)), churn=1, confidence=0.8, cost="MEDIUM")

    def score(**changes):
        return only(cand(**(base | changes))).score

    assert score(after=snap(20.0, (0.0, 0.0, 0.0))) > score()
    assert score(churn=2) < score() < score(churn=0)
    assert score(cost="HIGH") < score() < score(cost="LOW")
    assert score(confidence=0.9) > score() > score(confidence=0.7)
    for churn, cost, confidence in itertools.product((0, 1, 3), ("LOW", "MEDIUM", "HIGH"), (0.0, 0.5, 1.0)):
        assert 0.0 <= score(churn=churn, cost=cost, confidence=confidence) <= 1.0


def test_inputs_are_not_modified():
    item = cand()
    before_nodes = dict(item.before.node_blast)

    rank_candidates([item])

    assert dict(item.before.node_blast) == before_nodes


def test_nothing_to_rank_returns_nothing():
    assert rank_candidates([]) == []


@pytest.mark.parametrize("kwargs", [{"target_share": math.nan}, {"target_share": 1.5}, {"disruption_discount": -0.1},
                                    {"resource_discount": math.inf}, {"disruption_discount": None}])
def test_invalid_weights_are_refused(kwargs):
    with pytest.raises(ValueError):
        RankingWeights(**kwargs)


def test_every_result_can_be_reported_with_its_components_and_the_formula_version():
    (result,) = rank_candidates([cand(churn=1)])
    report = result.to_dict()

    assert report["rank"] == 1 and report["excluded"] is None
    assert {"targetReduction", "clusterReduction", "benefit", "placementChurn", "resourceFraction",
            "confidence", "survivabilityBefore", "survivabilityAfter"} <= set(report["components"])
    assert ranking.FORMULA_VERSION


# --- the module stays a pure, side-effect-free calculation -----------------------------------------------

def test_the_ranking_module_neither_predicts_failures_nor_touches_the_cluster_or_an_apply_path():
    source = inspect.getsource(ranking)

    for forbidden in ("failure_impact_prediction", "integrations", "kubernetes", "approval", "httpx", "open("):
        assert forbidden not in source.replace("Kubernetes", "").replace("kubernetes.io", ""), forbidden


# --- the least restrictive strategy wins a tie ------------------------------------------------------------------

def test_a_spread_beats_required_anti_affinity_when_everything_else_is_equal():
    from tests.support.member4_scenarios import catalogue

    live = next(c for c in catalogue() if c.name == "soft-anti-affinity-hardened").scenario.evaluate()
    ranking = live.evidence["ranking"]["candidates"]

    spread = ranking["rec-productpage-view-reviews-topology-spread"]
    anti = ranking["rec-productpage-view-reviews-pod-anti-affinity"]
    assert spread["score"] == pytest.approx(anti["score"])                  # the same placement, the same prediction
    assert (spread["rank"], anti["rank"]) == (1, 2)                          # so the less restrictive change is selected
    assert live.report.selected_recommendation_id == "rec-productpage-view-reviews-topology-spread"


def test_an_unlisted_strategy_never_beats_a_known_one_on_a_tie():
    from app.engine.recommendation_evaluation_validation.ranking import RESTRICTIVENESS

    assert max(RESTRICTIVENESS.values()) < len(RESTRICTIVENESS)
    assert RESTRICTIVENESS["TOPOLOGY_SPREAD"] < RESTRICTIVENESS["POD_ANTI_AFFINITY"] < RESTRICTIVENESS["REPLICA_INCREASE"]
