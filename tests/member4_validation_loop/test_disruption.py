"""The guard before every kill: essential workloads block, others need acknowledgement, evidence gaps refuse, health is rechecked."""

import pytest

from app.engine.recommendation_evaluation_validation.validation_loop.disruption import (
    ACKNOWLEDGE,
    BLOCK,
    NODE_LOCAL,
    REMAINS,
    PodFact,
    analyse,
    health_problems,
    make_guard,
)
from app.engine.recommendation_evaluation_validation.validation_loop.loop import KillBlocked
from tests.support.member4_harness import bookinfo_scenario

APP = "bookinfo"


def pod(name, node, *, ns="kube-system", kind="ReplicaSet", owner=None, ready=True, phase="Running"):
    owner_name = owner or (name.rsplit("-", 1)[0] + "-abc12" if kind == "ReplicaSet" else name)
    return PodFact(ns, name, node, ready, kind, owner_name, phase)


def by_workload(report):
    return {f.workload: f for f in report.findings}


def test_an_essential_workload_with_all_its_ready_pods_on_the_worker_blocks():
    report = analyse("w1", [pod("coredns-1", "w1"), pod("coredns-2", "w1")], APP)

    assert report.blocks and by_workload(report)["kube-system/Deployment/coredns"].severity == BLOCK


def test_an_essential_workload_that_keeps_a_ready_pod_elsewhere_remains_available_and_is_not_blocked():
    report = analyse("w1", [pod("coredns-1", "w1"), pod("coredns-2", "w2")], APP)

    assert by_workload(report)["kube-system/Deployment/coredns"].severity == REMAINS and not report.blocks


def test_a_replica_elsewhere_that_is_not_ready_does_not_count():
    report = analyse("w1", [pod("coredns-1", "w1"), pod("coredns-2", "w2", ready=False)], APP)

    assert report.blocks


def test_the_single_replica_storage_provisioner_blocks_in_its_critical_namespace():
    report = analyse("w1", [pod("local-path-provisioner-1", "w1", ns="local-path-storage")], APP)

    assert by_workload(report)["local-path-storage/Deployment/local-path-provisioner"].severity == BLOCK


def test_a_daemonset_pod_with_siblings_on_other_nodes_is_node_local_and_expected():
    pods = [pod("kube-proxy-a", "w1", kind="DaemonSet", owner="kube-proxy"), pod("kube-proxy-b", "w2", kind="DaemonSet", owner="kube-proxy")]

    assert by_workload(analyse("w1", pods, APP))["kube-system/DaemonSet/kube-proxy"].severity == NODE_LOCAL


def test_a_daemonset_whose_only_ready_pod_is_on_the_worker_is_not_waved_through():
    report = analyse("w1", [pod("ingress-a", "w1", kind="DaemonSet", owner="ingress"),
                            pod("ingress-b", "w2", kind="DaemonSet", owner="ingress", ready=False)], APP)

    assert by_workload(report)["kube-system/DaemonSet/ingress"].severity == BLOCK


def test_a_non_critical_workload_outside_the_application_must_be_acknowledged_even_with_replicas_elsewhere():
    report = analyse("w1", [pod("db-1", "w1", ns="other-app"), pod("db-2", "w2", ns="other-app")], APP)

    finding = by_workload(report)["other-app/Deployment/db"]
    assert finding.severity == ACKNOWLEDGE and finding.ready_elsewhere == 1 and not report.blocks


def test_a_bare_pod_is_blocking_in_a_critical_namespace_and_to_acknowledge_elsewhere():
    critical = analyse("w1", [PodFact("kube-system", "debug", "w1", True)], APP)
    other = analyse("w1", [PodFact("scratch", "debug", "w1", True)], APP)

    assert critical.blocks and other.to_acknowledge and not other.blocks


def test_the_applications_own_namespace_is_the_experiment_not_a_disruption():
    assert analyse("w1", [pod("reviews-1", "w1", ns=APP)], APP).findings == []


def test_a_pod_whose_readiness_is_unknown_is_treated_as_essential():
    report = analyse("w1", [PodFact("other-app", "x-1", "w1", None, "ReplicaSet", "x-abc12")], APP)

    assert report.blocks and "could not be read" in report.blocks[0].reason


def test_the_critical_namespaces_are_configurable_without_changing_code():
    pods = [pod("istiod-1", "w1", ns="istio-system")]

    assert not analyse("w1", pods, APP).blocks
    assert analyse("w1", pods, APP, critical_namespaces=("kube-system", "istio-system")).blocks


def test_pods_on_other_workers_are_not_findings():
    assert analyse("w1", [pod("coredns-1", "w2")], APP).findings == []


# --- the guard ---------------------------------------------------------------------------------------------------

def healthy_graph():
    return bookinfo_scenario("distributed").deployment.model_copy(deep=True)


def guard(pods, graph=None, **options):
    return make_guard(read_pods=lambda: [vars_of(p) for p in pods], read_graph=lambda: graph or healthy_graph(), namespace=APP, **options)


def vars_of(p):
    return {"namespace": p.namespace, "name": p.name, "node": p.node, "ready": p.ready, "ownerKind": p.owner_kind,
            "ownerName": p.owner_name, "phase": p.phase}


def test_a_safe_kill_passes_the_guard():
    guard([pod("coredns-1", "w1"), pod("coredns-2", "w2")])("w1", "c1")


def test_an_essential_workload_blocks_the_kill_and_an_acknowledgement_cannot_override_it():
    pods = [pod("coredns-1", "w1")]

    with pytest.raises(KillBlocked) as plain:
        guard(pods)("w1", "c1")
    with pytest.raises(KillBlocked) as acknowledged:
        guard(pods, acknowledged=frozenset({"kube-system/Deployment/coredns"}))("w1", "c1")

    assert plain.value.code == acknowledged.value.code == "ESSENTIAL_WORKLOAD"


def test_a_non_critical_workload_blocks_until_it_is_acknowledged_by_name():
    pods = [pod("db-1", "w1", ns="other-app"), pod("coredns-1", "w1"), pod("coredns-2", "w2")]

    with pytest.raises(KillBlocked) as refused:
        guard(pods)("w1", "c1")
    assert refused.value.code == "ACKNOWLEDGEMENT_MISSING" and "other-app/Deployment/db" in refused.value.message

    guard(pods, acknowledged=frozenset({"other-app/Deployment/db"}))("w1", "c1")


def test_a_workload_that_appeared_after_the_approval_is_not_covered_by_it():
    pods = [pod("db-1", "w1", ns="other-app"), pod("new-1", "w1", ns="another-app")]

    with pytest.raises(KillBlocked) as refused:
        guard(pods, acknowledged=frozenset({"other-app/Deployment/db"}))("w1", "c1")
    assert "another-app/Deployment/new" in refused.value.message


def test_a_read_that_fails_refuses_the_kill():
    def broken():
        raise RuntimeError("API unreachable")

    with pytest.raises(KillBlocked) as refused:
        make_guard(read_pods=broken, read_graph=healthy_graph, namespace=APP)("w1", "c1")
    assert refused.value.code == "EVIDENCE_INCOMPLETE"


def test_an_empty_pod_list_is_incomplete_evidence_not_permission():
    with pytest.raises(KillBlocked) as refused:
        make_guard(read_pods=lambda: [], read_graph=healthy_graph, namespace=APP)("w1", "c1")
    assert refused.value.code == "EVIDENCE_INCOMPLETE"


def test_health_is_rechecked_every_time_and_a_not_ready_node_refuses():
    graph = healthy_graph()
    graph.nodes[0].ready = False
    calls = []

    def read_graph():
        calls.append(1)
        return graph

    checking = make_guard(read_pods=lambda: [vars_of(pod("coredns-1", "w2"))], read_graph=read_graph, namespace=APP)
    for _ in range(2):
        with pytest.raises(KillBlocked) as refused:
            checking("w1", "c1")
        assert refused.value.code == "CLUSTER_NOT_HEALTHY"
    assert len(calls) == 2


def test_an_application_pod_that_is_not_ready_makes_the_baseline_unclean():
    graph = healthy_graph()
    graph.services[0].pods[0].ready = False

    assert any("not Ready" in p for p in health_problems(graph, APP))


def test_the_disposable_cluster_may_skip_the_outside_namespace_check_but_never_the_health_check():
    graph = healthy_graph()
    graph.nodes[0].ready = False
    skipping = make_guard(read_pods=lambda: [], read_graph=lambda: graph, namespace=APP, check_outside_namespace=False)

    with pytest.raises(KillBlocked) as refused:
        skipping("w1", "c1")
    assert refused.value.code == "CLUSTER_NOT_HEALTHY"
