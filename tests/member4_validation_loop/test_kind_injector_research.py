"""Docker Desktop stays blocked for the kind injector unless an explicit authorization names the exact connected cluster."""

import pytest

from app.engine.recommendation_evaluation_validation.validation import ResearchClusterAuthorization
from app.integrations.kind.node_fault import FaultRefused, KindWorkerFaultInjector
from app.integrations.kind.recovery import CommandResult


def runner(args):
    return CommandResult(1, "", "not used")


def build(cluster, workers, tmp_path, **options):
    return KindWorkerFaultInjector(cluster, workers, tmp_path, runner=runner, **options)


AUTHORIZATION = ResearchClusterAuthorization("run-approval:r1:abc", "uid-desktop", "The designated research cluster may be stopped.")


def test_the_docker_desktop_cluster_is_refused_by_default(tmp_path):
    with pytest.raises(FaultRefused) as refused:
        build("desktop", ["desktop-worker"], tmp_path)
    assert refused.value.code == "CLUSTER_REFUSED"


def test_an_authorization_for_the_exact_connected_cluster_opens_it_and_only_its_workers(tmp_path):
    injector = build("desktop", ["desktop-worker", "desktop-worker2"], tmp_path, research_authorization=AUTHORIZATION,
                     research_cluster_uid="uid-desktop")

    assert injector.cluster == "desktop"
    with pytest.raises(FaultRefused) as control_plane:
        build("desktop", ["desktop-control-plane"], tmp_path, research_authorization=AUTHORIZATION, research_cluster_uid="uid-desktop")
    assert control_plane.value.code == "NODE_NOT_ALLOWED"


@pytest.mark.parametrize("uid", [None, "", "some-other-uid"])
def test_an_authorization_does_not_open_a_cluster_whose_uid_differs_or_is_unknown(tmp_path, uid):
    with pytest.raises(FaultRefused) as refused:
        build("desktop", ["desktop-worker"], tmp_path, research_authorization=AUTHORIZATION, research_cluster_uid=uid)
    assert refused.value.code == "CLUSTER_REFUSED"


def test_an_authorization_without_a_reason_or_an_authorizer_is_not_enough(tmp_path):
    for incomplete in (ResearchClusterAuthorization("", "uid-desktop", "reason"), ResearchClusterAuthorization("x", "uid-desktop", " ")):
        with pytest.raises(FaultRefused):
            build("desktop", ["desktop-worker"], tmp_path, research_authorization=incomplete, research_cluster_uid="uid-desktop")


def test_the_single_node_docker_desktop_cluster_is_never_a_target_even_with_an_authorization(tmp_path):
    with pytest.raises(FaultRefused):
        build("docker-desktop", ["docker-desktop-worker"], tmp_path, research_authorization=AUTHORIZATION,
              research_cluster_uid="uid-desktop")


def test_the_authorization_does_not_change_how_any_other_cluster_is_treated(tmp_path):
    assert build("resilience-validation", ["resilience-validation-worker"], tmp_path, research_authorization=AUTHORIZATION,
                 research_cluster_uid="uid-desktop").cluster == "resilience-validation"


def test_a_runner_that_says_it_is_real_makes_the_evidence_real_and_any_other_is_a_double(tmp_path):
    class RealRunner:
        evidence_class = "REAL_CLUSTER"

        def __call__(self, args):
            return CommandResult(0, "", "")

    assert KindWorkerFaultInjector("resilience-validation", ["resilience-validation-worker"], tmp_path,
                                   runner=RealRunner()).evidence_class == "REAL_CLUSTER"
    assert build("resilience-validation", ["resilience-validation-worker"], tmp_path).evidence_class == "TEST_DOUBLE"
