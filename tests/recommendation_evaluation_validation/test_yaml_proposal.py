"""The YAML proposal: a minimal Deployment patch for an approved-eligible candidate, and nothing otherwise."""

import dataclasses

import pytest
import yaml

from app.engine.recommendation_evaluation_validation.approval import LIVE_EVIDENCE_SOURCE
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import SchedulingEvidence
from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from app.shared.schemas.deployment import TopologySpreadConstraintModel
from tests.support.member4_harness import (
    HOSTNAME,
    OFFLINE_SOURCE,
    Scenario,
    bookinfo_scenario,
    bookinfo_workflow,
    make_deployment,
    make_node,
    make_service,
    make_workflow,
    no_taints,
)

REVIEWS = "rec-productpage-view-reviews-topology-spread"


def documents(proposal):
    return [doc for doc in yaml.safe_load_all(proposal.yaml) if doc is not None]


def as_live(live):
    """The same evaluation as if it had been computed from the connected cluster (only the label differs)."""
    return dataclasses.replace(live, evidence={**live.evidence, "source": LIVE_EVIDENCE_SOURCE})


def test_the_selected_candidate_becomes_a_minimal_deployment_patch():
    live = bookinfo_scenario("concentrated").evaluate()

    proposal = build_yaml_proposal(live, REVIEWS)

    assert proposal.status == "GENERATED"
    (doc,) = documents(proposal)
    assert (doc["apiVersion"], doc["kind"]) == ("apps/v1", "Deployment")
    assert doc["metadata"] == {"name": "reviews", "namespace": "offline-benchmark"}
    # Minimal: the patch touches the pod template's spread constraints and nothing else.
    assert set(doc) == {"apiVersion", "kind", "metadata", "spec"}
    assert set(doc["spec"]) == {"template"} and set(doc["spec"]["template"]) == {"spec"}
    assert doc["spec"]["template"]["spec"] == {"topologySpreadConstraints": [{
        "maxSkew": 1, "topologyKey": HOSTNAME, "whenUnsatisfiable": "DoNotSchedule",
        "labelSelector": {"matchLabels": {"app": "reviews"}}}]}


def test_the_patch_adds_no_replicas_and_changes_no_other_field():
    live = bookinfo_scenario("concentrated").evaluate()

    (doc,) = documents(build_yaml_proposal(live, REVIEWS))

    assert "replicas" not in doc["spec"]
    assert "affinity" not in doc["spec"]["template"]["spec"]


def test_it_never_claims_to_move_pods_and_discloses_the_replacement_separately():
    live = bookinfo_scenario("concentrated").evaluate()

    proposal = build_yaml_proposal(live, REVIEWS)

    # reviews: w1, w1, w1 -> predicted one pod stays, two get a new node
    assert proposal.pod_replacement["included"] is False
    assert len(proposal.pod_replacement["pods"]) == 2
    assert "does not move" in proposal.pod_replacement["note"]
    assert any("rolls out" in line for line in proposal.disclosures)
    assert any("scheduler" in line for line in proposal.disclosures)
    assert "NOT APPLIED" in proposal.yaml and "does NOT move running pods" in proposal.yaml


def test_offline_evidence_gives_text_that_is_not_applicable():
    proposal = build_yaml_proposal(bookinfo_scenario("concentrated").evaluate(), REVIEWS)

    assert proposal.evidence_source == OFFLINE_SOURCE
    assert proposal.applicable is False
    assert "not read from the connected cluster" in proposal.reason


def test_live_evidence_makes_the_same_text_applicable():
    live = as_live(bookinfo_scenario("concentrated").evaluate())

    proposal = build_yaml_proposal(live, REVIEWS)

    assert proposal.status == "GENERATED" and proposal.applicable is True and proposal.reason is None


def test_the_text_is_deterministic_and_bound_to_the_proposal_fingerprint():
    first = build_yaml_proposal(bookinfo_scenario("concentrated").evaluate(), REVIEWS)
    second = build_yaml_proposal(bookinfo_scenario("concentrated").evaluate(), REVIEWS)

    assert first.yaml == second.yaml and first.sha256 == second.sha256
    assert first.fingerprint and first.fingerprint in first.yaml


def test_building_a_proposal_changes_nothing():
    live = bookinfo_scenario("concentrated").evaluate()
    before = (live.report.model_dump(), dict(live.evidence), dict(live.details))

    build_yaml_proposal(live, REVIEWS)

    assert (live.report.model_dump(), dict(live.evidence), dict(live.details)) == before


def test_insufficient_evidence_never_gets_yaml():
    scenario = bookinfo_scenario("concentrated")
    live = scenario.evaluate(evidence=SchedulingEvidence())     # node taints were not read

    proposal = build_yaml_proposal(live, REVIEWS)

    assert proposal.status == "REFUSED" and proposal.code == "INSUFFICIENT_EVIDENCE"
    assert proposal.yaml is None and proposal.sha256 is None and proposal.applicable is False
    assert proposal.patches == ()


def test_an_infeasible_candidate_never_gets_yaml():
    # One replica cannot be spread over two nodes, so the verified answer is INFEASIBLE.
    nodes = [make_node(n, zone=n) for n in ("w1", "w2", "w3")]
    deployment = make_deployment([make_service("productpage", ["w2", "w3"]), make_service("details", ["w2", "w3"]),
                                  make_service("reviews", ["w1"])], nodes)
    scenario = Scenario("single-replica", "", bookinfo_workflow(), deployment, no_taints(deployment), "w1")
    live = scenario.evaluate()
    (candidate,) = [c for c in live.report.recommendations if c.service == "reviews"]

    proposal = build_yaml_proposal(live, candidate.id)

    assert candidate.feasible is False
    assert proposal.status == "REFUSED" and proposal.code == "INFEASIBLE"
    assert proposal.yaml is None and proposal.applicable is False


def test_a_candidate_that_is_not_ranked_gets_no_yaml():
    live = bookinfo_scenario("concentrated").evaluate()
    ratings = "rec-productpage-view-ratings-topology-spread"      # feasible, but no predicted improvement

    proposal = build_yaml_proposal(live, ratings)

    assert proposal.status == "REFUSED" and proposal.code == "NOT_ELIGIBLE"
    assert proposal.yaml is None


def test_an_unknown_candidate_gets_no_yaml():
    proposal = build_yaml_proposal(bookinfo_scenario("concentrated").evaluate(), "rec-nope")

    assert proposal.status == "REFUSED" and proposal.code == "CANDIDATE_NOT_FOUND" and proposal.yaml is None


def test_a_deployment_that_cannot_be_named_is_not_guessed():
    live = bookinfo_scenario("concentrated").evaluate()
    for service in live.details["currentDeployment"]["services"]:
        service["deploymentNames"] = []

    proposal = build_yaml_proposal(live, REVIEWS)

    assert proposal.status == "REFUSED" and proposal.code == "DEPLOYMENT_UNRESOLVED" and proposal.yaml is None


def test_a_service_already_spread_by_hostname_is_not_silently_rewritten():
    nodes = [make_node(n, zone=n) for n in ("w1", "w2", "w3")]
    existing = TopologySpreadConstraintModel(
        max_skew=2, topology_key=HOSTNAME, when_unsatisfiable="DoNotSchedule",
        label_selector={"matchLabels": {"app": "reviews"}})
    deployment = make_deployment([
        make_service("productpage", ["w2", "w3"]), make_service("details", ["w2", "w3"]),
        make_service("reviews", ["w1", "w1", "w1"], topology_spread=[existing])], nodes)
    live = Scenario("already", "", bookinfo_workflow(), deployment, no_taints(deployment), "w1").evaluate()
    candidate = next((c for c in live.report.recommendations if c.service == "reviews"), None)

    proposal = build_yaml_proposal(live, candidate.id)

    # A strategic merge on the same topologyKey would overwrite that constraint: a different change to propose.
    assert proposal.status == "REFUSED" and proposal.code == "ALREADY_CONSTRAINED" and proposal.yaml is None


def test_a_combination_becomes_one_patch_per_deployment_in_a_fixed_order():
    # details and reviews are both on w1 and both mandatory: spreading either alone changes nothing.
    nodes = [make_node(n, zone=n) for n in ("w1", "w2", "w3")]
    deployment = make_deployment([
        make_service("productpage", ["w1", "w2", "w3"]), make_service("details", ["w1", "w1"]),
        make_service("reviews", ["w1", "w1", "w1"]), make_service("ratings", ["w2", "w3"])], nodes)
    live = Scenario("pair", "", bookinfo_workflow(), deployment, no_taints(deployment), "w1").evaluate()
    selected = live.report.selected_recommendation_id

    proposal = build_yaml_proposal(live, selected)

    assert selected.endswith("combination")
    assert proposal.status == "GENERATED"
    assert [d["metadata"]["name"] for d in documents(proposal)] == ["details", "reviews"]
    assert proposal.yaml.count("topologySpreadConstraints") == 2
