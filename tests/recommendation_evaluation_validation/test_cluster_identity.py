"""A cluster is a validation target only if it is the one a person designated, identified by something it cannot fake."""

import pytest

from app.engine.recommendation_evaluation_validation.validation import (
    TEST_ENVIRONMENT,
    ClusterIdentity,
    DisposableClusterDesignation,
    ResearchClusterAuthorization,
    ValidationRefusedError,
    verify_cluster,
)

UID = "6f1e2c1a-0000-4000-8000-0000000000aa"
OTHER_UID = "9b9b9b9b-0000-4000-8000-0000000000bb"


def identity(**overrides) -> ClusterIdentity:
    values = dict(context="kind-resilience-test", api_server="https://127.0.0.1:6443", cluster_uid=UID,
                  node_names=("rt-control-plane", "rt-worker", "rt-worker2", "rt-worker3"))
    return ClusterIdentity(**{**values, **overrides})


def designation(**overrides) -> DisposableClusterDesignation:
    values = dict(name="kind-resilience-test", api_server="https://127.0.0.1:6443", cluster_uid=UID,
                  namespace="resilience-validation", designated_by="asesh")
    return DisposableClusterDesignation(**{**values, **overrides})


def refused(code, **kwargs):
    with pytest.raises(ValidationRefusedError) as raised:
        verify_cluster(**kwargs)
    assert raised.value.code == code, raised.value
    return raised.value


def test_the_designated_cluster_becomes_a_test_target_named_after_the_designation():
    target = verify_cluster(identity(), designation())

    assert target.name == "kind-resilience-test" and target.environment == TEST_ENVIRONMENT


def test_a_cluster_with_a_different_uid_is_refused_even_when_every_name_matches():
    refused("CLUSTER_IDENTITY_MISMATCH", identity=identity(cluster_uid=OTHER_UID), designation=designation())


def test_a_cluster_whose_uid_could_not_be_read_is_refused_not_assumed():
    refused("CLUSTER_IDENTITY_UNVERIFIED", identity=identity(cluster_uid=None), designation=designation())


def test_a_designation_that_does_not_pin_the_uid_designates_nothing():
    refused("CLUSTER_NOT_DESIGNATED", identity=identity(), designation=designation(cluster_uid=""))
    refused("CLUSTER_NOT_DESIGNATED", identity=identity(), designation=designation(designated_by="  "))


def test_a_different_api_server_is_refused():
    refused("CLUSTER_IDENTITY_MISMATCH", identity=identity(api_server="https://10.0.0.9:6443"), designation=designation())


def test_a_trailing_slash_or_letter_case_in_the_address_is_not_a_difference():
    assert verify_cluster(identity(api_server="HTTPS://127.0.0.1:6443/"), designation())


def test_a_context_the_designation_names_must_be_the_one_in_use():
    refused("CLUSTER_IDENTITY_MISMATCH", identity=identity(context="kind-other"), designation=designation(context="kind-resilience-test"))


def test_a_node_set_the_designation_names_must_match_exactly():
    nodes = ("rt-control-plane", "rt-worker", "rt-worker2", "rt-worker3")
    assert verify_cluster(identity(), designation(node_names=nodes))
    refused("CLUSTER_IDENTITY_MISMATCH", identity=identity(node_names=nodes + ("surprise",)), designation=designation(node_names=nodes))


def test_system_namespaces_cannot_be_the_experiment_namespace():
    for namespace in ("kube-system", "kube-public", "kube-node-lease", ""):
        refused("CLUSTER_NOT_DESIGNATED", identity=identity(), designation=designation(namespace=namespace))


# --- the normal research cluster ---------------------------------------------------------------------------------

DOCKER_DESKTOP = dict(context="docker-desktop", node_names=("docker-desktop",), api_server="https://127.0.0.1:6443")


@pytest.mark.parametrize("research", [
    dict(context="docker-desktop"),
    dict(context="kind-x", node_names=("docker-desktop",)),
    dict(context="kind-x", node_names=("desktop-control-plane", "desktop-worker", "desktop-worker2")),
], ids=["context", "single-node-name", "multi-node-names"])
def test_the_docker_desktop_research_cluster_is_refused_even_when_it_is_designated(research):
    error = refused("RESEARCH_CLUSTER_REFUSED", identity=identity(**research), designation=designation())

    assert "separately authorized" in error.message


def test_it_is_refused_when_the_designation_itself_names_the_research_cluster():
    refused("RESEARCH_CLUSTER_REFUSED", identity=identity(**DOCKER_DESKTOP), designation=designation(name="docker-desktop"))


def test_a_separate_authorization_for_that_exact_cluster_lifts_only_that_refusal():
    override = ResearchClusterAuthorization(confirmed_by="asesh", cluster_uid=UID, reason="no other cluster this week")

    target = verify_cluster(identity(**DOCKER_DESKTOP), designation(), research_override=override)

    assert target.environment == TEST_ENVIRONMENT


def test_the_authorization_does_not_stand_in_for_a_designation_or_another_cluster():
    elsewhere = ResearchClusterAuthorization(confirmed_by="asesh", cluster_uid=OTHER_UID, reason="r")
    blank = ResearchClusterAuthorization(confirmed_by=" ", cluster_uid=UID, reason="r")
    unexplained = ResearchClusterAuthorization(confirmed_by="asesh", cluster_uid=UID, reason="")

    for override in (elsewhere, blank, unexplained):
        refused("RESEARCH_CLUSTER_REFUSED", identity=identity(**DOCKER_DESKTOP), designation=designation(), research_override=override)
    # the authorization names the cluster, but the designation does not: still not a designated test cluster
    refused("CLUSTER_IDENTITY_MISMATCH", identity=identity(**DOCKER_DESKTOP),
            designation=designation(cluster_uid=OTHER_UID),
            research_override=ResearchClusterAuthorization("asesh", UID, "r"))


def test_the_research_markers_are_configurable_not_hardcoded():
    custom = frozenset({"rt-worker"})

    refused("RESEARCH_CLUSTER_REFUSED", identity=identity(), designation=designation(), research_node_names=custom)
