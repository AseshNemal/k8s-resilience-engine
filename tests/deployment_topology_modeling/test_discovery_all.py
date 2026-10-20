"""Overview discovery across every namespace: one ``discover()`` result per namespace, plus totals."""

import pytest

from app.engine.deployment_topology_modeling.discovery import DiscoveryError, discover
from app.engine.deployment_topology_modeling.discovery_all import discover_all
from app.integrations.kubernetes.client import ConnectionFailedError, ForbiddenError, NotConnectedError
from tests.support.fake_cluster import FakeClusterSource, multi_namespace_cluster, node, pod


def test_every_namespace_with_workloads_gets_its_own_result_sorted_by_name():
    result = discover_all(multi_namespace_cluster())

    assert [r.namespace for r in result.namespaces] == ["resilience-test", "shop"]
    assert result.status == "ok"


def test_each_namespace_result_is_exactly_what_discover_returns_for_it():
    source = multi_namespace_cluster()
    result = discover_all(source)

    for namespace_result in result.namespaces:
        single = discover(namespace_result.namespace, source)
        assert [s.name for s in namespace_result.graph.services] == [s.name for s in single.graph.services]
        assert namespace_result.status == single.status


def test_a_concentrated_workload_shows_its_real_placement():
    result = discover_all(multi_namespace_cluster())
    payment = next(r for r in result.namespaces if r.namespace == "resilience-test").graph.services[0]

    assert payment.name == "paymentservice"
    assert {p.node for p in payment.pods} == {"worker-1"}
    assert payment.replica_concentration == pytest.approx(1.0)


def test_system_namespaces_are_excluded_by_default_and_listed():
    result = discover_all(multi_namespace_cluster())

    assert result.excluded_namespaces == ["kube-system"]
    assert "kube-system" not in [r.namespace for r in result.namespaces]


def test_system_namespaces_can_be_included():
    result = discover_all(multi_namespace_cluster(), include_system=True)

    assert [r.namespace for r in result.namespaces] == ["kube-system", "resilience-test", "shop"]
    assert result.excluded_namespaces == []


def test_totals_count_workers_once_and_add_up_services_and_unresolved():
    result = discover_all(multi_namespace_cluster())

    assert result.totals.worker_nodes == 2  # the control plane is not a failure domain
    assert result.totals.services == 3  # web, db, paymentservice
    assert result.totals.unresolved == 0


def test_an_unresolved_pod_makes_the_whole_overview_partial_and_is_counted():
    source = multi_namespace_cluster()
    source.pods["shop"].append(pod("stray-1", "worker-1", app="not-a-deployment"))

    result = discover_all(source)

    assert result.status == "partial"
    assert result.totals.unresolved >= 1
    assert next(r for r in result.namespaces if r.namespace == "shop").status == "partial"


def test_a_cluster_with_no_workloads_is_a_valid_empty_overview_that_still_counts_workers():
    result = discover_all(FakeClusterSource(nodes=[node("worker-1"), node("worker-2")]))

    assert result.status == "empty"
    assert result.namespaces == []
    assert result.totals.worker_nodes == 2
    assert result.totals.services == 0


def test_only_system_namespaces_means_empty_not_a_failure():
    source = FakeClusterSource(
        nodes=[node("worker-1")],
        pods={"kube-system": [pod("coredns-1", "worker-1", app="coredns")]},
        deployments={"kube-system": []},
    )

    result = discover_all(source)

    assert result.status == "empty"
    assert result.excluded_namespaces == ["kube-system"]


@pytest.mark.parametrize(("error", "code"), [
    (NotConnectedError("x"), "CLUSTER_NOT_CONNECTED"),
    (ConnectionFailedError("x"), "CLUSTER_CONNECTION_FAILED"),
    (ForbiddenError("list", "pods"), "CLUSTER_FORBIDDEN"),
    (RuntimeError("secret detail"), "DISCOVERY_FAILED"),
])
def test_an_unreadable_cluster_fails_explicitly_and_never_returns_a_result(error, code):
    with pytest.raises(DiscoveryError) as caught:
        discover_all(FakeClusterSource(error=error))

    assert caught.value.code == code
    assert "secret detail" not in str(caught.value)
