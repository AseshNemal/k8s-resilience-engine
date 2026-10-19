"""The in-memory cluster must offer what a real one does: Services, ReplicaSets and consistent ownership."""

import pytest

from tests.support.fake_cluster import FakeClusterSource, bookinfo, two_node_shop

NAMESPACE = "bookinfo"


def selects(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return bool(selector) and all(labels.get(k) == v for k, v in selector.items())


def test_bookinfo_has_four_services_that_select_their_pods():
    cluster = bookinfo()
    services = {s.name: s for s in cluster.list_services(NAMESPACE)}
    pods = cluster.list_pods(NAMESPACE)

    assert set(services) == {"productpage", "details", "ratings", "reviews"}
    for name, svc in services.items():
        assert [p.name for p in pods if selects(svc.selector, p.labels)], f"{name} selects no pod"


def test_every_bookinfo_pod_resolves_to_an_existing_deployment():
    cluster = bookinfo()
    replicasets = {r.name: r for r in cluster.list_replicasets(NAMESPACE)}
    deployments = {d.name for d in cluster.list_deployments(NAMESPACE)}

    for pod in cluster.list_pods(NAMESPACE):
        assert pod.owner_kind == "ReplicaSet"
        assert replicasets[pod.owner_name].deployment_name in deployments


def test_bookinfo_mirrors_the_live_placement_on_workers():
    cluster = bookinfo()
    placed = {p.labels["app"]: p.node for p in cluster.list_pods(NAMESPACE)}

    assert placed == {
        "productpage": "resilience-research-worker2",
        "details": "resilience-research-worker2",
        "ratings": "resilience-research-worker",
        "reviews": "resilience-research-worker3",
    }
    assert {n.name for n in cluster.list_nodes() if n.control_plane} == {"resilience-research-control-plane"}


def test_bookinfo_reviews_can_span_two_deployments():
    cluster = bookinfo(reviews_versions=("v1", "v2"))
    reviews = next(s for s in cluster.list_services(NAMESPACE) if s.name == "reviews")
    replicasets = {r.name: r for r in cluster.list_replicasets(NAMESPACE)}
    behind = {
        replicasets[p.owner_name].deployment_name
        for p in cluster.list_pods(NAMESPACE) if selects(reviews.selector, p.labels)
    }

    assert behind == {"reviews-v1", "reviews-v2"}
    assert {d.name for d in cluster.list_deployments(NAMESPACE)} >= {"reviews-v1", "reviews-v2"}


def test_error_injection_reaches_services_and_replicasets():
    cluster = FakeClusterSource(error=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        cluster.list_services(NAMESPACE)
    with pytest.raises(RuntimeError):
        cluster.list_replicasets(NAMESPACE)
    assert cluster.calls == [f"services:{NAMESPACE}", f"replicasets:{NAMESPACE}"]


def test_unknown_namespace_lists_are_empty():
    cluster = bookinfo()

    assert cluster.list_services("nope") == []
    assert cluster.list_replicasets("nope") == []


def test_existing_two_node_shop_is_unchanged():
    cluster = two_node_shop()

    assert [p.name for p in cluster.list_pods("shop")] == ["web-1", "web-2", "db-1"]
    assert [d.name for d in cluster.list_deployments("shop")] == ["web", "db"]
    assert cluster.list_services("shop") == []


def test_lists_are_copies_so_callers_cannot_change_the_cluster():
    cluster = bookinfo()

    cluster.list_services(NAMESPACE).clear()
    cluster.list_replicasets(NAMESPACE).clear()

    assert len(cluster.list_services(NAMESPACE)) == 4
    assert cluster.list_replicasets(NAMESPACE)
