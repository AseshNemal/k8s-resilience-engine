"""How far the ranking moves when the experimental weights move (Member 4, Phase 2).

The defaults (alpha 0.7, d 0.3, r 0.3) are provisional and unproven. These tests do not show they are right;
they show how sensitive the ranking is to them, on two populations.

Both populations are GENERATED from fixed random seeds. Nothing here was observed on a running cluster, and
none of it is an experiment on real deployments: the clusters, workflows and placements are synthetic. "Real" in
the first bullet means only that the real Phase 1 evaluator and Component 3 code process them.

* generated clusters: random topologies and workflows, run through the real Phase 1 evaluator and Component 3's
  public portfolio predictor (a graded blast radius, the "portfolio-equal-weight" basis). Single-workflow
  ranking never had two rankable candidates in the generated clusters (see the last test): one workflow needs
  every failed service fixed at once, so a single fix rarely helps alone. Today the weights cannot change a
  single-workflow outcome in these scenarios;
* stress: candidates sampled from the generated clusters' marginals but made independent, so benefit and
  placement churn genuinely trade off. This is where the weights can matter.

Everything is seeded, so the numbers are reproducible. Thresholds sit beyond what was measured, with margin.
Measured when written: top candidate unchanged across the whole box on 38 of 392 generated clusters with a contest;
on 1500 stress sets a 0.2 move changes the top candidate in 7-10% (d), 1-3% (alpha), 8% (r) of them; d = 0.3 is the
most central d, while alpha 0.5, 0.7 and 0.9 are indistinguishable (so alpha = 0.7 is a design choice).
"""

import random
import statistics
from dataclasses import replace

import pytest

from app.engine.failure_impact_prediction.portfolio_impact import predict_portfolio_failure_impact
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.candidate_generation import CandidateSpec
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import FeasibilityStatus, SchedulingEvidence
from app.engine.recommendation_evaluation_validation.ranking import (
    EvaluatedCandidate,
    ImpactSnapshot,
    RankingWeights,
    rank_candidates,
)
from app.engine.recommendation_evaluation_validation.service import evaluate_candidate
from app.engine.recommendation_evaluation_validation.simulation_result import SimulationResult
from app.shared.schemas.deployment import (
    DeclaredPlacementIntent,
    DeploymentGraph,
    NodeModel,
    PodModel,
    ServicePlacement,
)
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph

HOST = "kubernetes.io/hostname"
BASIS = "portfolio-equal-weight"
DEFAULT = RankingWeights()
BOX = [RankingWeights(a, d, r) for a in (0.5, 0.7, 0.9) for d in (0.1, 0.3, 0.5) for r in (0.1, 0.3, 0.5)]


def _cluster(rng):
    nodes = [NodeModel(name=f"n{i}", zone="z", ready=True, cpu_available=8.0, memory_available=16.0,
                       labels={HOST: f"n{i}"}) for i in range(rng.randint(3, 5))]
    services = []
    for j in range(rng.randint(4, 7)):
        replicas, home = rng.randint(1, 4), f"n{rng.randrange(len(nodes))}"
        placement = [home if rng.random() < 0.65 else f"n{rng.randrange(len(nodes))}" for _ in range(replicas)]
        services.append(ServicePlacement(
            name=f"s{j}", declared_replicas=replicas, ready_replicas=replicas,
            pods=[PodModel(name=f"s{j}-{i}", node=n, ready=True, owner_kind="ReplicaSet")
                  for i, n in enumerate(placement)],
            declared_intent=DeclaredPlacementIntent(selector={"app": f"s{j}"},
                                                     resource_requests=[{"cpu": "100m", "memory": "128Mi"}])))
    graph = DeploymentGraph(nodes=nodes, services=services)
    workflows = []
    for k in range(rng.randint(3, 5)):
        members = rng.sample([s.name for s in services], rng.randint(2, 3))
        workflows.append(WorkflowGraph(workflow_id=f"w{k}", criticality=0.5, mandatory_services=members,
                                       edges=[WorkflowEdge(source=members[0], target=m) for m in members[1:]]))
    return graph, workflows


def _snapshot(report_by_node, target):
    return ImpactSnapshot(failed_node=target, blast_radius=report_by_node[target].blast_radius,
                          survivability=report_by_node[target].survivability,
                          node_blast={n: r.blast_radius for n, r in report_by_node.items()}, basis=BASIS)


def generated_population(seeds):
    """Lists of feasible candidates, one list per generated cluster, with Component 3 before/after impact."""
    population = []
    for seed in range(seeds):
        graph, workflows = _cluster(random.Random(seed))
        evidence = SchedulingEvidence(node_taints={n.name: [] for n in graph.nodes})
        before = {n.name: predict_portfolio_failure_impact(workflows, graph, n.name) for n in graph.nodes}
        target = max(before, key=lambda n: (before[n].blast_radius, tuple(-ord(c) for c in n)))
        if before[target].blast_radius == 0:
            continue
        candidates = []
        for service in graph.services:
            ready = [p for p in service.pods if p.ready]
            if len(ready) < 2 or {p.node for p in ready} != {target}:
                continue   # the generator only proposes services that go down with the assessed node
            simulated = evaluate_candidate(graph, CandidateSpec("TOPOLOGY_SPREAD", service.name, "sensitivity"), evidence)
            if not simulated.feasible:
                continue
            after = {n.name: predict_portfolio_failure_impact(workflows, simulated.simulated_deployment, n.name)
                     for n in graph.nodes}
            candidates.append(EvaluatedCandidate(
                id=service.name, type="TOPOLOGY_SPREAD", service=service.name, simulation=simulated,
                before=_snapshot(before, target), after=_snapshot(after, target),
                ready_replicas=len(ready), confidence=1.0, resource_cost="LOW"))
        population.append(candidates)
    return population


def stress_population(sets, target_reductions, seed=11):
    """Independent benefit, risk elsewhere, placement churn and cost: the trade-offs the weights decide."""
    rng, population = random.Random(seed), []
    for _ in range(sets):
        candidates = []
        for i in range(rng.randint(2, 4)):
            t, elsewhere = rng.choice(target_reductions), [rng.uniform(0, 100) for _ in range(3)]
            before = {"n0": 100.0, "n1": elsewhere[0], "n2": elsewhere[1], "n3": elsewhere[2]}
            after = before | {"n0": 100.0 * (1 - t)}
            snap = lambda v: ImpactSnapshot("n0", v["n0"], 100.0 - v["n0"], v, BASIS)  # noqa: E731
            simulated = SimulationResult(
                "TOPOLOGY_SPREAD", f"c{i}", True, "", 1.0, 0.0, 1.0, None,
                feasibility_status=FeasibilityStatus.FEASIBLE,
                pods_to_recreate=tuple(f"p{k}" for k in range(rng.randint(0, 4))))
            candidates.append(EvaluatedCandidate(
                id=f"c{i}", type="TOPOLOGY_SPREAD", service=f"c{i}", simulation=simulated, before=snap(before),
                after=snap(after), ready_replicas=4, confidence=1.0,
                resource_cost=rng.choice(["LOW", "LOW", "MEDIUM", "HIGH"])))
        population.append(candidates)
    return population


def rankable(population, weights=DEFAULT):
    return [c for c in population if sum(r.rank is not None for r in rank_candidates(c, weights)) >= 2]


def top(candidates, weights):
    return rank_candidates(candidates, weights)[0].id


def flip_rate(population, changed):
    return statistics.mean(top(c, changed) != top(c, DEFAULT) for c in population)


@pytest.fixture(scope="module")
def generated():
    return generated_population(seeds=400)


@pytest.fixture(scope="module")
def generated_reductions(generated):
    values = sorted({round(r.components.target_reduction, 3) for c in generated for r in rank_candidates(c) if r.components})
    return [v for v in values if v > 0]


@pytest.fixture(scope="module")
def stress(generated_reductions):
    return rankable(stress_population(1500, generated_reductions))


def test_on_generated_clusters_the_top_candidate_is_the_same_across_the_whole_weight_box(generated):
    contested = rankable(generated)

    assert len(contested) >= 20, "the seeded population should contain enough clusters with two rankable candidates"
    agreement = statistics.mean(top(c, w) == top(c, DEFAULT) for c in contested for w in BOX)
    assert agreement >= 0.95            # measured 1.00: benefit differences are larger than the discounts


def test_on_generated_clusters_ties_on_benefit_are_common_so_churn_and_cost_act_as_tie_breakers(generated):
    contested = rankable(generated)

    tied = statistics.mean(
        len({round(r.components.benefit, 6) for r in rank_candidates(c) if r.rank}) < sum(1 for r in rank_candidates(c) if r.rank)
        for c in contested)
    assert tied >= 0.25                 # measured about 0.45


def test_the_metric_relationships_claimed_in_the_design_hold_on_generated_clusters(generated):
    pairs = [(r.components.target_reduction, r.components.cluster_reduction, r.components.placement_churn)
             for c in generated for r in rank_candidates(c) if r.rank]
    t, s, churn = zip(*pairs)

    assert statistics.correlation(t, s) > 0.6          # the assessed node is one of the nodes s averages over
    assert abs(statistics.correlation(t, churn)) < 0.3  # churn is close to independent of benefit
    assert statistics.mean(r < 0.999 for r in (b / a for a, b in zip(t, s) if a > 0)) > 0.5   # s is usually the smaller


def test_removing_a_candidate_from_a_generated_cluster_never_reorders_the_others(generated):
    for candidates in rankable(generated):
        full = [r.id for r in rank_candidates(candidates) if r.rank]
        for dropped in candidates:
            rest = [r.id for r in rank_candidates([c for c in candidates if c.id != dropped.id]) if r.rank]
            assert rest == [i for i in full if i != dropped.id]


@pytest.mark.parametrize("field, values, limit", [
    ("disruption_discount", (0.1, 0.5), 0.12),     # measured 0.072 and 0.096
    ("target_share", (0.5, 0.9), 0.06),            # measured 0.029 and 0.013
    ("resource_discount", (0.1, 0.5), 0.11),       # measured 0.085 and 0.079
])
def test_moving_one_weight_by_0_2_changes_the_top_candidate_in_few_trade_off_cases(stress, field, values, limit):
    assert len(stress) >= 500
    for value in values:
        assert flip_rate(stress, replace(DEFAULT, **{field: value})) <= limit


def test_the_weights_do_matter_when_the_trade_off_is_real(stress):
    # Honest limit of the design: with no churn discount the top candidate changes in a visible share of the
    # stress sets, so the default d is a judgement, not a measurement.
    assert 0.05 <= flip_rate(stress, replace(DEFAULT, disruption_discount=0.0)) <= 0.30     # measured 0.105


def test_d_equal_0_3_is_the_most_central_discount_and_the_data_does_not_choose_alpha(stress):
    sample = stress[:300]

    def agreement(weights):
        return statistics.mean(statistics.mean(top(c, weights) == top(c, other) for c in sample) for other in BOX)

    by_setting = {(a, d): agreement(RankingWeights(a, d, 0.3)) for a in (0.5, 0.7, 0.9) for d in (0.1, 0.3, 0.5)}

    at_default_d = [v for (a, d), v in by_setting.items() if d == 0.3]
    elsewhere = [v for (a, d), v in by_setting.items() if d != 0.3]
    assert min(at_default_d) > max(elsewhere)                 # measured 0.910 against at most 0.896
    assert max(at_default_d) - min(at_default_d) < 0.02       # alpha 0.5, 0.7 and 0.9 differ by about 0.004


def test_single_workflow_ranking_never_had_two_rankable_candidates_in_the_generated_clusters():
    # A property of these generated scenarios, not an observation of any running cluster. Over 400 seeds, 343
    # generated clusters had at least one candidate and none had two rankable ones; this replays the first 120.
    contested = 0
    for seed in range(120):
        graph, workflows = _cluster(random.Random(seed))
        evidence = SchedulingEvidence(node_taints={n.name: [] for n in graph.nodes})
        before = {n.name: predict_failure_impact(workflows[0], graph, n.name) for n in graph.nodes}
        target = max(before, key=lambda n: (before[n].blast_radius, tuple(-ord(c) for c in n)))
        if before[target].blast_radius == 0:
            continue

        def snapshot(reports):
            return ImpactSnapshot(target, reports[target].blast_radius, reports[target].survivability,
                                  {n: r.blast_radius for n, r in reports.items()}, "single-workflow")

        rankable_here = 0
        for service in graph.services:
            ready = [p for p in service.pods if p.ready]
            if len(ready) < 2 or {p.node for p in ready} != {target}:
                continue
            simulated = evaluate_candidate(graph, CandidateSpec("TOPOLOGY_SPREAD", service.name, "x"), evidence)
            if not simulated.feasible:
                continue
            after = {n.name: predict_failure_impact(workflows[0], simulated.simulated_deployment, n.name)
                     for n in graph.nodes}
            candidate = EvaluatedCandidate(service.name, "TOPOLOGY_SPREAD", service.name, simulated,
                                           snapshot(before), snapshot(after), len(ready), 1.0, "LOW")
            rankable_here += rank_candidates([candidate])[0].rank is not None
        contested += rankable_here >= 2

    assert contested == 0       # measured 0 of 343 generated clusters over 400 seeds
