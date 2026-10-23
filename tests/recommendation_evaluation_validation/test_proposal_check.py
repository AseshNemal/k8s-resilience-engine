"""The YAML a run is about to write is checked against the Deployments that are in the cluster right now."""

import copy

import pytest
import yaml

from app.engine.recommendation_evaluation_validation.validation import ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation.proposal_check import check_proposal
from app.engine.recommendation_evaluation_validation.yaml_proposal import GENERATED, build_yaml_proposal
from tests.support.member4_approvals import as_live
from tests.support.member4_fake_gateway import live_deployments
from tests.support.member4_scenarios import catalogue

NAMESPACE = "offline-benchmark"
CASES = {case.name: case for case in catalogue()}


def generated(case_name):
    """(YAML text, the Deployments as the cluster would hold them) for the case's own candidate."""
    case = CASES[case_name]
    live = as_live(case.scenario.evaluate())
    if case.strategy == "COMBINATION":      # a pair has no single service; the case is the engine's own selection
        recommendation_id = live.report.selected_recommendation_id
    else:
        recommendation_id = next(c.id for c in live.report.recommendations
                                 if c.type == case.strategy and c.service == case.service)
    proposal = build_yaml_proposal(live, recommendation_id)
    assert proposal.status == GENERATED, proposal.reason
    return proposal.yaml, live_deployments(case.scenario.deployment)


def versions(deployments):
    return {name: d.resource_version for name, d in deployments.items()}


def check(text, deployments, **options):
    return check_proposal(text, deployments, namespace=NAMESPACE, baseline_versions=versions(deployments), **options)


def refused(code, text, deployments, **options):
    with pytest.raises(ValidationRefusedError) as raised:
        check(text, deployments, **options)
    assert raised.value.code == code, raised.value
    return raised.value


def mutate(text, edit):
    documents = [doc for doc in yaml.safe_load_all(text) if doc]
    edit(documents)
    return yaml.safe_dump_all(documents, sort_keys=False)


# --- what the generator really emits is accepted ------------------------------------------------------------------

@pytest.mark.parametrize("case_name, count", [
    ("concentrated-replicas", 1), ("single-replica-increase", 1), ("soft-anti-affinity-hardened", 1),
    ("multi-service-mandatory", 2), ("pair-of-replica-increases", 2), ("spread-and-replica-increase", 2)])
def test_every_proposal_the_generator_produces_passes_the_check(case_name, count):
    text, deployments = generated(case_name)

    patches = check(text, deployments)

    assert len(patches) == count and all(p.name in deployments for p in patches)


# --- only a Deployment's placement fields may be written -----------------------------------------------------------

def test_a_document_that_is_not_a_deployment_is_refused():
    text, deployments = generated("concentrated-replicas")
    refused("PROPOSAL_NOT_ALLOWED", mutate(text, lambda docs: docs[0].update(kind="Service")), deployments)
    refused("PROPOSAL_NOT_ALLOWED", mutate(text, lambda docs: docs[0].update(apiVersion="v1")), deployments)


def test_an_extra_field_anywhere_in_the_patch_is_refused():
    text, deployments = generated("concentrated-replicas")
    edits = [
        lambda docs: docs[0]["spec"].update(strategy={"type": "Recreate"}),
        lambda docs: docs[0]["spec"]["template"].update(metadata={"labels": {"x": "y"}}),
        lambda docs: docs[0]["spec"]["template"]["spec"].update(containers=[{"name": "c", "image": "evil"}]),
        lambda docs: docs[0]["spec"]["template"]["spec"].update(nodeName="w1"),
        lambda docs: docs[0]["metadata"].update(labels={"a": "b"}),
        lambda docs: docs[0].update(status={"replicas": 9}),
    ]
    for edit in edits:
        refused("PROPOSAL_NOT_ALLOWED", mutate(text, edit), deployments)


def test_a_patch_in_another_namespace_is_refused():
    text, deployments = generated("concentrated-replicas")
    refused("PROPOSAL_WRONG_NAMESPACE", mutate(text, lambda docs: docs[0]["metadata"].update(namespace="default")), deployments)


def test_a_deployment_the_cluster_does_not_have_is_refused():
    text, deployments = generated("concentrated-replicas")
    refused("PROPOSAL_UNKNOWN_DEPLOYMENT", mutate(text, lambda docs: docs[0]["metadata"].update(name="ghost")), deployments)


def test_nothing_to_write_and_unreadable_text_are_refused():
    _, deployments = generated("concentrated-replicas")
    refused("PROPOSAL_EMPTY", "# only a comment\n", deployments)
    refused("PROPOSAL_UNPARSEABLE", "a: [unclosed", deployments)


def test_the_same_deployment_twice_is_refused():
    text, deployments = generated("concentrated-replicas")
    refused("PROPOSAL_NOT_ALLOWED", mutate(text, lambda docs: docs.append(copy.deepcopy(docs[0]))), deployments)


# --- the change must still mean what it meant when it was verified -----------------------------------------------

def test_a_spread_that_selects_other_pods_is_refused():
    text, deployments = generated("concentrated-replicas")

    def edit(docs):
        docs[0]["spec"]["template"]["spec"]["topologySpreadConstraints"][0]["labelSelector"]["matchLabels"] = {"app": "other"}
    refused("PROPOSAL_SELECTOR_MISMATCH", mutate(text, edit), deployments)


def test_a_spread_on_a_key_other_than_the_hostname_is_refused():
    text, deployments = generated("concentrated-replicas")

    def edit(docs):
        docs[0]["spec"]["template"]["spec"]["topologySpreadConstraints"][0]["topologyKey"] = "topology.kubernetes.io/zone"
    refused("PROPOSAL_NOT_ALLOWED", mutate(text, edit), deployments)


def test_replicas_may_only_go_up_and_by_a_configured_amount():
    text, deployments = generated("single-replica-increase")
    current = deployments["reviews"].replicas

    for replicas, code in ((current, "PROPOSAL_REPLICAS_INVALID"), (current - 1, "PROPOSAL_REPLICAS_INVALID"),
                           ("2", "PROPOSAL_REPLICAS_INVALID"), (current + 10, "PROPOSAL_REPLICAS_INVALID")):
        refused(code, mutate(text, lambda docs, r=replicas: docs[0]["spec"].update(replicas=r)), deployments)
    assert check(mutate(text, lambda docs: docs[0]["spec"].update(replicas=current + 3)), deployments, max_added_replicas=3)
    refused("PROPOSAL_REPLICAS_INVALID", mutate(text, lambda docs: docs[0]["spec"].update(replicas=current + 4)),
            deployments, max_added_replicas=3)


def test_a_constraint_that_would_overwrite_one_already_in_the_cluster_is_refused():
    text, deployments = generated("concentrated-replicas")
    live = deployments["reviews"]
    already = {**live.__dict__, "topology_spread_constraints": ({"topologyKey": "kubernetes.io/hostname", "maxSkew": 2},)}
    changed = {**deployments, "reviews": type(live)(**already)}

    refused("PROPOSAL_WOULD_OVERWRITE", text, changed)


def test_a_deployment_that_changed_since_the_baseline_was_captured_is_refused_as_drifted():
    text, deployments = generated("concentrated-replicas")
    baseline = versions(deployments)
    live = deployments["reviews"]
    moved = {**deployments, "reviews": type(live)(**{**live.__dict__, "resource_version": "rv-99", "generation": 7})}

    with pytest.raises(ValidationRefusedError) as raised:
        check_proposal(text, moved, namespace=NAMESPACE, baseline_versions=baseline)

    assert raised.value.code == "DEPLOYMENT_DRIFTED" and "reviews" in raised.value.message


def test_a_deployment_with_no_baseline_version_is_refused_not_assumed_unchanged():
    text, deployments = generated("concentrated-replicas")

    with pytest.raises(ValidationRefusedError) as raised:
        check_proposal(text, deployments, namespace=NAMESPACE, baseline_versions={})

    assert raised.value.code == "DEPLOYMENT_DRIFTED"
