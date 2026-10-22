"""Required pod anti-affinity and a replica increase, judged by the same constraint search as a plain spread."""

from collections import Counter

import pytest

from app.engine.recommendation_evaluation_validation.candidate_generation import (
    CandidateContextBuilder,
    CandidateSpec,
    generate_candidates,
)
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    FeasibilityStatus,
    SchedulingEvidence,
    check_topology_spread_feasibility,
)
from app.engine.recommendation_evaluation_validation.service import evaluate_candidate
from app.engine.recommendation_evaluation_validation.strategy_feasibility import (
    ADDED_MARKER,
    check_pod_anti_affinity_feasibility,
    check_replica_increase_feasibility,
    has_soft_self_anti_affinity,
)
from app.shared.schemas.deployment import TopologySpreadConstraintModel
from app.shared.schemas.failure_impact import FailureImpactReport, RootCause
from tests.support.member4_harness import (
    HOSTNAME,
    make_deployment,
    make_node,
    make_service,
    no_taints,
)

FEASIBLE, INFEASIBLE, INSUFFICIENT = (FeasibilityStatus.FEASIBLE, FeasibilityStatus.INFEASIBLE,
                                      FeasibilityStatus.INSUFFICIENT_EVIDENCE)
DEDICATED = {"key": "dedicated", "value": "x", "effect": "NoSchedule"}
SOFT = {"preferredDuringSchedulingIgnoredDuringExecution": [
    {"weight": 100, "podAffinityTerm": {"labelSelector": {"matchLabels": {"app": "svc"}}, "topologyKey": HOSTNAME}}]}
REQUIRED = {"requiredDuringSchedulingIgnoredDuringExecution": [
    {"labelSelector": {"matchLabels": {"app": "svc"}}, "topologyKey": HOSTNAME}]}


def graph(placement, *, nodes=("n1", "n2", "n3"), node_options=None, **service_options):
    options = node_options or {}
    built = [make_node(name, **options.get(name, {})) for name in nodes]
    return make_deployment([make_service("svc", placement, **service_options)], built)


# --- required pod anti-affinity -----------------------------------------------------------------------------

def test_anti_affinity_puts_each_replica_on_its_own_node():
    g = graph(["n1", "n1", "n1"])

    result = check_pod_anti_affinity_feasibility(g, "svc", no_taints(g))

    assert result.status is FEASIBLE and sorted(result.placement.values()) == ["n1", "n2", "n3"]
    assert "podAntiAffinity" in result.assumptions[0] and "different node" in result.reason


def test_anti_affinity_with_more_replicas_than_nodes_is_infeasible_where_a_spread_is_not():
    g = graph(["n1"] * 4)

    anti = check_pod_anti_affinity_feasibility(g, "svc", no_taints(g))
    spread = check_topology_spread_feasibility(g, "svc", no_taints(g))

    assert spread.status is FEASIBLE and Counter(spread.placement.values()).most_common(1)[0][1] == 2
    assert anti.status is INFEASIBLE and "POD_ANTI_AFFINITY" in {f.constraint for f in anti.findings}


@pytest.mark.parametrize("replicas", [2, 3, 4, 5])
@pytest.mark.parametrize("nodes", [2, 3, 4])
@pytest.mark.parametrize("dead", [0, 1])
def test_anti_affinity_admits_a_subset_of_what_a_spread_admits_and_places_the_same(replicas, nodes, dead):
    names = tuple(f"n{i}" for i in range(1, nodes + 1))
    options = {names[-1]: {"ready": False}} if dead and nodes > 2 else {}
    g = graph(["n1"] * replicas, nodes=names, node_options=options)

    anti = check_pod_anti_affinity_feasibility(g, "svc", no_taints(g))
    spread = check_topology_spread_feasibility(g, "svc", no_taints(g))

    if anti.status is FEASIBLE:
        assert spread.status is FEASIBLE, "anti-affinity succeeded where a spread did not"
        assert sorted(anti.placement.values()) == sorted(spread.placement.values())
        assert len(set(anti.placement.values())) == replicas        # one per node
    if spread.status is INFEASIBLE:
        assert anti.status is INFEASIBLE


def test_anti_affinity_taints_that_were_never_read_are_insufficient_evidence():
    g = graph(["n1", "n1"])

    assert check_pod_anti_affinity_feasibility(g, "svc", SchedulingEvidence()).status is INSUFFICIENT


def test_anti_affinity_respects_taints_capacity_and_selectors_like_a_spread_does():
    taints = SchedulingEvidence(node_taints={"n1": [], "n2": [DEDICATED], "n3": [DEDICATED]})
    g = graph(["n1", "n1"])

    assert check_pod_anti_affinity_feasibility(g, "svc", taints).status is INFEASIBLE


def test_anti_affinity_does_not_replace_terms_the_deployment_already_requires():
    g = graph(["n1", "n1"], pod_anti_affinity_rules=REQUIRED)

    result = check_pod_anti_affinity_feasibility(g, "svc", no_taints(g))

    assert result.status is INFEASIBLE and "ALREADY_CONSTRAINED" in {f.constraint for f in result.findings}


def test_anti_affinity_without_the_pod_labels_is_insufficient_evidence_not_a_guess():
    g = graph(["n1", "n1"], intent=False)

    result = check_pod_anti_affinity_feasibility(g, "svc", no_taints(g))

    assert result.status is INSUFFICIENT and any("pod labels" in item for item in result.missing_evidence)


def test_anti_affinity_keeps_the_soft_terms_and_leaves_the_graph_unchanged():
    g = graph(["n1", "n1"], pod_anti_affinity_rules=SOFT)
    before = g.model_dump()

    result = check_pod_anti_affinity_feasibility(g, "svc", no_taints(g))

    assert result.status is FEASIBLE and g.model_dump() == before


def test_soft_anti_affinity_is_recognised_only_for_the_hostname_key_and_the_services_own_pods():
    own = make_service("svc", ["n1"], pod_anti_affinity_rules=SOFT)
    zone = make_service("svc", ["n1"], pod_anti_affinity_rules={"preferredDuringSchedulingIgnoredDuringExecution": [
        {"weight": 1, "podAffinityTerm": {"labelSelector": {"matchLabels": {"app": "svc"}}, "topologyKey": "zone"}}]})
    other = make_service("svc", ["n1"], pod_anti_affinity_rules={"preferredDuringSchedulingIgnoredDuringExecution": [
        {"weight": 1, "podAffinityTerm": {"labelSelector": {"matchLabels": {"app": "else"}}, "topologyKey": HOSTNAME}}]})

    assert has_soft_self_anti_affinity(own) is True
    assert has_soft_self_anti_affinity(zone) is False and has_soft_self_anti_affinity(other) is False
    assert has_soft_self_anti_affinity(make_service("svc", ["n1"])) is False


# --- replica increase ----------------------------------------------------------------------------------------

def test_a_single_replica_gets_a_second_one_on_another_node_and_the_real_one_stays():
    g = graph(["n1"])

    result = check_replica_increase_feasibility(g, "svc", no_taints(g))

    assert result.status is FEASIBLE and result.feasible
    assert set(result.placement) == {"svc-0", f"svc{ADDED_MARKER}1"}
    assert result.placement["svc-0"] == "n1" and result.placement[f"svc{ADDED_MARKER}1"] != "n1"
    assert result.pods_to_recreate == ()         # the added replica is not placement churn
    assert "adds 1" in result.reason and "CPU and memory" in result.reason
    assert any("HorizontalPodAutoscaler" in item for item in result.not_modeled)


def test_a_replica_increase_is_not_proposed_when_more_than_one_replica_is_ready():
    g = graph(["n1", "n1"], declared=2)

    result = check_replica_increase_feasibility(g, "svc", no_taints(g))

    assert result.status is INFEASIBLE and "REPLICA_INCREASE_NOT_NECESSARY" in {f.constraint for f in result.findings}


def test_a_replica_increase_is_not_proposed_for_a_service_whose_only_replica_is_not_ready():
    g = graph(["n1"], ready=[False])

    assert check_replica_increase_feasibility(g, "svc", no_taints(g)).status is INFEASIBLE


def test_a_replica_increase_needs_room_for_the_extra_replica_on_another_node():
    g = graph(["n1"], node_options={"n2": {"cpu": 0.05}, "n3": {"cpu": 0.05}}, requests=[{"cpu": "500m", "memory": "128Mi"}])

    result = check_replica_increase_feasibility(g, "svc", no_taints(g))

    assert result.status is INFEASIBLE and "NODE_CAPACITY" in {f.constraint for f in result.findings}


def test_a_replica_increase_respects_taints():
    blocked = SchedulingEvidence(node_taints={"n1": [], "n2": [DEDICATED], "n3": [DEDICATED]})
    g = graph(["n1"])

    assert check_replica_increase_feasibility(g, "svc", blocked).status is INFEASIBLE
    assert check_replica_increase_feasibility(g, "svc", SchedulingEvidence()).status is INSUFFICIENT


def test_a_replica_increase_on_a_one_node_cluster_cannot_survive_that_node():
    g = graph(["n1"], nodes=("n1",))

    result = check_replica_increase_feasibility(g, "svc", no_taints(g))

    assert result.status is INFEASIBLE and result.single_node_resilience is False


def test_a_replica_increase_does_not_rewrite_an_existing_hostname_spread():
    existing = TopologySpreadConstraintModel(max_skew=3, topology_key=HOSTNAME, when_unsatisfiable="ScheduleAnyway",
                                             label_selector={"matchLabels": {"app": "svc"}})
    g = graph(["n1"], topology_spread=[existing])

    result = check_replica_increase_feasibility(g, "svc", no_taints(g))

    assert result.status is INFEASIBLE and "ALREADY_CONSTRAINED" in {f.constraint for f in result.findings}


def test_checking_a_replica_increase_leaves_the_graph_untouched():
    g = graph(["n1"])
    before = g.model_dump()

    check_replica_increase_feasibility(g, "svc", no_taints(g))

    assert g.model_dump() == before


# --- simulation: the virtual copy ------------------------------------------------------------------------------

def test_a_simulated_replica_increase_adds_a_pod_to_a_copy_and_not_to_the_original():
    g = graph(["n1"])
    before = g.model_dump()

    result = evaluate_candidate(g, CandidateSpec("REPLICA_INCREASE", "svc", "r"), no_taints(g))

    service = next(s for s in result.simulated_deployment.services if s.name == "svc")
    assert result.feasible and result.added_replicas == (f"svc{ADDED_MARKER}1",)
    assert len(service.pods) == 2 and service.declared_replicas == 2 and service.ready_replicas == 2
    assert len({p.node for p in service.pods}) == 2 and service.concentrated is False
    assert service.declared_intent.declared_replicas == 2
    assert g.model_dump() == before


def test_a_simulated_anti_affinity_places_the_replicas_on_distinct_nodes_on_a_copy():
    g = graph(["n1", "n1", "n1"])
    before = g.model_dump()

    result = evaluate_candidate(g, CandidateSpec("POD_ANTI_AFFINITY", "svc", "r"), no_taints(g))

    service = next(s for s in result.simulated_deployment.services if s.name == "svc")
    assert result.feasible and len({p.node for p in service.pods}) == 3 and g.model_dump() == before


def test_an_infeasible_replica_increase_is_not_simulated():
    g = graph(["n1"], nodes=("n1",))

    result = evaluate_candidate(g, CandidateSpec("REPLICA_INCREASE", "svc", "r"), no_taints(g))

    assert result.feasible is False and result.simulated_deployment is None and result.added_replicas == ()


# --- when each strategy is offered -----------------------------------------------------------------------------

def context(g, service="svc"):
    report = FailureImpactReport(
        workflow_id="wf", failed_node="n1", blast_radius=100.0, survivability=0.0, failed_services=[service],
        failed_workflows=["wf"], root_causes=[RootCause(type="REPLICA_CONCENTRATION", service=service,
                                                        failure_domain="n1", concentration=1.0)])
    return CandidateContextBuilder.build(g, report)


def types(g):
    return sorted(c.recommendation_type for c in generate_candidates(context(g)))


def test_a_concentrated_service_with_several_replicas_gets_only_a_spread():
    assert types(graph(["n1", "n1", "n1"])) == ["TOPOLOGY_SPREAD"]


def test_a_single_replica_service_gets_a_replica_increase_as_well_as_the_spread_that_cannot_help():
    assert types(graph(["n1"])) == ["REPLICA_INCREASE", "TOPOLOGY_SPREAD"]


def test_soft_anti_affinity_adds_a_hardening_candidate_and_required_anti_affinity_does_not():
    assert types(graph(["n1", "n1"], pod_anti_affinity_rules=SOFT)) == ["POD_ANTI_AFFINITY", "TOPOLOGY_SPREAD"]
    assert types(graph(["n1", "n1"], pod_anti_affinity_rules=REQUIRED)) == ["TOPOLOGY_SPREAD"]


def test_nothing_extra_is_offered_without_the_facts_to_judge_by():
    empty = make_deployment([], [])

    assert [c.recommendation_type for c in generate_candidates(context(empty))] == ["TOPOLOGY_SPREAD"]


def test_the_subset_sweep_is_not_vacuous():
    both, spread_only, neither = 0, 0, 0
    for replicas in (2, 3, 4, 5):
        for nodes in (2, 3, 4):
            names = tuple(f"n{i}" for i in range(1, nodes + 1))
            g = graph(["n1"] * replicas, nodes=names)
            anti = check_pod_anti_affinity_feasibility(g, "svc", no_taints(g)).status
            spread = check_topology_spread_feasibility(g, "svc", no_taints(g)).status
            both += anti is FEASIBLE and spread is FEASIBLE
            spread_only += anti is not FEASIBLE and spread is FEASIBLE
            neither += anti is not FEASIBLE and spread is not FEASIBLE

    assert both and spread_only        # the two verdicts differ somewhere, and agree somewhere


# --- pairs share the capacity the first change used -------------------------------------------------------------

def two_single_replicas(w2_cpu):
    """details and productpage each run one replica on w1, both required; w2 is the only node with room."""
    from tests.support.member4_harness import Scenario, bookinfo_workflow

    nodes = [make_node("w1"), make_node("w2", cpu=w2_cpu), make_node("w3", cpu=0.0)]
    g = make_deployment([make_service("productpage", ["w1"], requests=[{"cpu": "500m", "memory": "128Mi"}]),
                         make_service("details", ["w1"], requests=[{"cpu": "500m", "memory": "128Mi"}]),
                         make_service("reviews", ["w2", "w3"]), make_service("ratings", ["w2", "w3"])], nodes)
    return Scenario("capacity-pair", "", bookinfo_workflow(), g, no_taints(g), "w1")


def pair(live):
    return next((c for c in live.report.recommendations if c.type == "COMBINATION"), None)


def test_two_replica_increases_that_need_the_same_scarce_room_are_not_promised_together():
    live = two_single_replicas(w2_cpu=0.5).evaluate()      # room for exactly one more 500m replica on w2, none on w3

    entries = live.evidence["combinations"]["entries"]
    assert pair(live) is None
    (entry,) = entries.values()
    assert entry["status"] == "INSUFFICIENT_EVIDENCE" and entry["excluded"] == "NOT_VERIFIED_JOINTLY"
    assert any("capacity" in attempt or "CAPACITY" in attempt for attempt in entry["attempts"])
    assert live.report.selected_recommendation_id is None        # neither single helps alone: nothing is promised


def test_with_room_for_both_the_pair_is_verified_and_the_capacity_is_shared_in_the_final_state():
    live = two_single_replicas(w2_cpu=1.0).evaluate()

    combo = pair(live)
    assert combo is not None and combo.feasible and live.report.selected_recommendation_id == combo.id
    final = live.details["candidates"][combo.id]["simulatedDeployment"]
    w2 = next(n for n in final["nodes"] if n["name"] == "w2")
    assert w2["cpuAvailable"] == pytest.approx(0.0)              # both added replicas are on w2 and have used its room
