"""The two further replica-increase candidates: +1 replica under a required anti-affinity, and +2 replicas under a spread.

They are judged by the same search as every other strategy, rank beside (never above) the minimal changes, and the YAML they
produce is the YAML the Apply whitelist accepts.
"""

import dataclasses
from collections import Counter

import pytest
import yaml

from app.engine.recommendation_evaluation_validation.approval import LIVE_EVIDENCE_SOURCE
from app.engine.recommendation_evaluation_validation.candidate_generation import CandidateSpec
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import FeasibilityStatus
from app.engine.recommendation_evaluation_validation.service import evaluate_candidate
from app.engine.recommendation_evaluation_validation.strategy_feasibility import (
    ADDED_MARKER,
    REPLICA_INCREASE_ANTI_AFFINITY_MODEL,
    REPLICA_INCREASE_PLUS_2_MODEL,
    check_replica_increase_anti_affinity_feasibility,
    check_replica_increase_plus_two_feasibility,
)
from app.engine.recommendation_evaluation_validation.validation.cluster_gateway import LiveDeployment
from app.engine.recommendation_evaluation_validation.validation.proposal_check import check_proposal
from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from app.shared.schemas.deployment import TopologySpreadConstraintModel
from tests.recommendation_evaluation_validation.test_strategy_feasibility import REQUIRED, graph
from tests.support.member4_scenarios import catalogue
from tests.support.member4_harness import HOSTNAME, bookinfo_scenario, no_taints

FEASIBLE, INFEASIBLE, INSUFFICIENT = (FeasibilityStatus.FEASIBLE, FeasibilityStatus.INFEASIBLE,
                                      FeasibilityStatus.INSUFFICIENT_EVIDENCE)
ANTI, PLUS_2 = "REPLICA_INCREASE_ANTI_AFFINITY", "REPLICA_INCREASE_PLUS_2"


# --- +1 replica under a required anti-affinity ---------------------------------------------------------------------------------

def test_one_more_replica_under_anti_affinity_gives_every_replica_its_own_node():
    g = graph(["n1", "n1", "n1"], nodes=("n1", "n2", "n3", "n4"))

    result = check_replica_increase_anti_affinity_feasibility(g, "svc", no_taints(g))

    assert result.status is FEASIBLE
    assert sorted(result.placement.values()) == ["n1", "n2", "n3", "n4"]            # four replicas, four nodes
    assert sum(ADDED_MARKER in name for name in result.placement) == 1
    assert "required podAntiAffinity" in result.assumptions[0]
    assert "1 added" in result.reason and "model prediction, not a scheduler guarantee" in result.reason


def test_anti_affinity_cannot_hold_more_replicas_than_there_are_nodes_and_says_so():
    g = graph(["n1", "n1", "n1"])                                                     # 3 replicas + 1 added, 3 nodes

    result = check_replica_increase_anti_affinity_feasibility(g, "svc", no_taints(g))

    assert result.status is INFEASIBLE and result.reason.startswith("Adding 1 replica(s) to svc with a required hostname anti-affinity")


# --- +2 replicas under a spread ------------------------------------------------------------------------------------------------

def test_two_more_replicas_are_spread_over_the_nodes():
    g = graph(["n1", "n1"])

    result = check_replica_increase_plus_two_feasibility(g, "svc", no_taints(g))

    assert result.status is FEASIBLE
    assert sum(ADDED_MARKER in name for name in result.placement) == 2
    assert max(Counter(result.placement.values()).values()) <= 2 and len(set(result.placement.values())) == 3   # four replicas, skew 1
    assert "topologySpreadConstraint" in result.assumptions[0]


def test_the_two_variants_have_their_own_models_so_an_approval_is_bound_to_the_one_that_was_verified():
    assert REPLICA_INCREASE_ANTI_AFFINITY_MODEL != REPLICA_INCREASE_PLUS_2_MODEL
    assert "+1" in REPLICA_INCREASE_ANTI_AFFINITY_MODEL and "+2" in REPLICA_INCREASE_PLUS_2_MODEL


# --- what they refuse to propose ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("check", [check_replica_increase_anti_affinity_feasibility, check_replica_increase_plus_two_feasibility])
@pytest.mark.parametrize("existing", ["required anti-affinity", "hostname spread"])
def test_neither_variant_rewrites_a_constraint_the_deployment_already_has(check, existing):
    options = ({"pod_anti_affinity_rules": REQUIRED} if existing == "required anti-affinity" else {"topology_spread": [
        TopologySpreadConstraintModel(max_skew=3, topology_key=HOSTNAME, when_unsatisfiable="ScheduleAnyway",
                                      label_selector={"matchLabels": {"app": "svc"}})]})
    g = graph(["n1", "n1"], **options)

    result = check(g, "svc", no_taints(g))

    assert result.status is INFEASIBLE and "ALREADY_CONSTRAINED" in {f.constraint for f in result.findings}


@pytest.mark.parametrize("check", [check_replica_increase_anti_affinity_feasibility, check_replica_increase_plus_two_feasibility])
def test_a_service_with_an_unready_replica_is_repaired_first_not_given_more_replicas(check):
    g = graph(["n1", "n1"])
    g.services[0].pods[1].ready = False

    result = check(g, "svc", no_taints(g))

    assert result.status is INFEASIBLE and "REPLICAS_NOT_SETTLED" in {f.constraint for f in result.findings}


def test_without_the_pod_labels_an_anti_affinity_term_cannot_be_written_and_the_verdict_is_undecided():
    g = graph(["n1", "n1"])
    g.services[0].declared_intent = None

    result = check_replica_increase_anti_affinity_feasibility(g, "svc", no_taints(g))

    assert result.status is INSUFFICIENT and result.missing_evidence


def test_a_service_the_graph_does_not_have_is_undecided_not_assumed():
    g = graph(["n1"])

    assert check_replica_increase_plus_two_feasibility(g, "ghost", no_taints(g)).status is INSUFFICIENT


# --- simulated, ranked, and written as YAML ----------------------------------------------------------------------------------------

def test_a_verified_variant_is_simulated_with_its_added_replicas_and_the_original_graph_is_untouched():
    g = graph(["n1", "n1", "n1"], nodes=("n1", "n2", "n3", "n4"))
    before = g.model_dump()

    result = evaluate_candidate(g, CandidateSpec(ANTI, "svc", "r"), no_taints(g))

    assert result.feasible and len(result.added_replicas) == 1
    assert len(result.simulated_deployment.services[0].pods) == 4 and result.simulated_deployment.services[0].declared_replicas == 4
    assert g.model_dump() == before


def test_the_variants_rank_beside_the_minimal_changes_and_never_above_the_one_that_does_the_same_for_less():
    case = next(c for c in catalogue() if c.name == "anti-affinity-too-strict")        # a spread works; so does +2; anti-affinity cannot
    live = case.scenario.evaluate()
    ranking = live.evidence["ranking"]["candidates"]

    spread, plus_2 = ranking["rec-productpage-view-reviews-topology-spread"], ranking["rec-productpage-view-reviews-replica-increase-plus-2"]

    assert live.report.selected_recommendation_id == "rec-productpage-view-reviews-topology-spread"
    assert spread["rank"] == 1 and plus_2["rank"] == 2 and plus_2["score"] < spread["score"]       # the same effect, twice the resource cost


def test_the_costs_are_stated_plus_one_is_medium_and_plus_two_is_high():
    live = bookinfo_scenario("concentrated").evaluate()
    cost = {c.type: c.resource_cost for c in live.report.recommendations}

    assert cost[ANTI] == "MEDIUM" and cost[PLUS_2] == "HIGH" and cost["TOPOLOGY_SPREAD"] == "LOW"


def as_live(live):
    return dataclasses.replace(live, evidence={**live.evidence, "source": LIVE_EVIDENCE_SOURCE})


def live_deployments(live):
    """What the cluster would hold today for each service's Deployment, read from the same evaluation the YAML came from."""
    found = {}
    for service in live.details["currentDeployment"]["services"]:
        intent = service.get("declaredIntent") or {}
        name = (service.get("deploymentNames") or [service["name"]])[0]
        found[name] = LiveDeployment(name=name, namespace=service.get("namespace") or "offline-benchmark", resource_version="1",
                                     generation=1, replicas=service["declaredReplicas"], selector=intent.get("selector") or {},
                                     pod_labels=intent.get("selector") or {})
    return found


@pytest.mark.parametrize(("type_name", "added"), [(ANTI, 1), (PLUS_2, 2)])
def test_the_yaml_of_a_variant_raises_the_replicas_and_adds_exactly_the_constraint_the_search_assumed(type_name, added):
    case = next(c for c in catalogue() if c.name == "single-replica")              # one replica on the failing node: every replica variant helps
    live = as_live(case.scenario.evaluate())
    candidate = next(c for c in live.report.recommendations if c.type == type_name)
    assert candidate.feasible and live.evidence["ranking"]["candidates"][candidate.id]["rank"] is not None

    proposal = build_yaml_proposal(live, candidate.id)

    assert proposal.status == "GENERATED", proposal.reason
    (doc,) = [d for d in yaml.safe_load_all(proposal.yaml) if d]
    declared = next(s["declaredReplicas"] for s in live.details["currentDeployment"]["services"] if s["name"] == candidate.service)
    assert doc["spec"]["replicas"] == declared + added
    pod_spec = doc["spec"]["template"]["spec"]
    if type_name == ANTI:
        assert set(pod_spec) == {"affinity"}
        assert pod_spec["affinity"]["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]["topologyKey"] == HOSTNAME
    else:
        assert set(pod_spec) == {"topologySpreadConstraints"}
    assert any(note for note in proposal.disclosures if "replicas" in note)                # the cost of the added replicas is disclosed
    # the text the engine writes is text the Apply whitelist accepts against the Deployments it came from
    deployments = live_deployments(live)
    patches = check_proposal(proposal.yaml, deployments, namespace=doc["metadata"]["namespace"],
                             baseline_versions={name: d.resource_version for name, d in deployments.items()})
    assert [p.name for p in patches] == [doc["metadata"]["name"]]
