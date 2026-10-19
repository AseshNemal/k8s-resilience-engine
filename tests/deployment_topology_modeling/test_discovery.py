"""Discovery must report real evidence, explicit failures, and never fall back to sample data."""

import pytest

from app.engine.deployment_topology_modeling import discovery
from app.engine.deployment_topology_modeling.discovery import DiscoveryError, discover
from app.integrations.kubernetes.client import (
    AuthFailedError,
    ConnectionFailedError,
    ForbiddenError,
    NotConnectedError,
)
from app.shared.sample_loader import load_sample_json
from tests.support.fake_cluster import (
    FakeClusterSource,
    deployment,
    node,
    pod,
    two_node_shop,
)

SAMPLE = load_sample_json("deployment.json")
SAMPLE_NODE_NAMES = {n["name"] for n in SAMPLE["nodes"]}
SAMPLE_SERVICE_NAMES = {s["name"] for s in SAMPLE["services"]}


@pytest.mark.parametrize(
    ("raised", "code", "http"),
    [
        (NotConnectedError("no kubeconfig"), "CLUSTER_NOT_CONNECTED", 503),
        (ConnectionFailedError("refused"), "CLUSTER_CONNECTION_FAILED", 502),
        (AuthFailedError("expired"), "CLUSTER_AUTH_FAILED", 502),
        (ForbiddenError("list", "pods"), "CLUSTER_FORBIDDEN", 502),
        (RuntimeError("boom with token abc123"), "DISCOVERY_FAILED", 502),
    ],
)
def test_every_failure_is_an_explicit_error_never_a_graph(raised, code, http):
    source = FakeClusterSource(error=raised)

    with pytest.raises(DiscoveryError) as caught:
        discover("shop", source=source)

    assert caught.value.code == code
    assert caught.value.http_status == http
    assert "abc123" not in caught.value.message  # unexpected errors are sanitised


def test_failure_through_the_default_source_never_yields_sample_topology(monkeypatch):
    monkeypatch.setattr(discovery, "get_cluster_source", lambda: FakeClusterSource(error=NotConnectedError("x")))

    with pytest.raises(DiscoveryError) as caught:
        discovery.discover("shop")

    assert caught.value.code == "CLUSTER_NOT_CONNECTED"


def test_missing_kubeconfig_with_the_real_source_is_not_connected():
    # conftest points KUBECONFIG at an absent file, so the real source finds no credential.
    with pytest.raises(DiscoveryError) as caught:
        discover("shop")

    assert caught.value.code == "CLUSTER_NOT_CONNECTED"


def test_ok_when_everything_is_resolved():
    result = discover("shop", source=two_node_shop())

    assert result.status == "ok"
    assert result.namespace == "shop"
    assert {n.name for n in result.graph.nodes} == {"worker-1", "worker-2"}
    assert {s.name for s in result.graph.services} == {"web", "db"}
    assert result.unresolved == []
    assert result.service_evidence["web"].complete is True
    assert not ({n.name for n in result.graph.nodes} & SAMPLE_NODE_NAMES)


def test_connected_cluster_with_no_workloads_is_a_valid_empty_state():
    source = FakeClusterSource(nodes=[node("worker-1"), node("worker-2")])

    result = discover("shop", source=source)

    assert result.status == "empty"
    assert result.graph.services == []
    assert [n.name for n in result.graph.nodes] == ["worker-1", "worker-2"]


def test_empty_namespace_is_flagged_as_not_verified_to_exist():
    result = discover("shop", source=FakeClusterSource(nodes=[node("worker-1")]))

    codes = [w.code for w in result.warnings]
    assert "NAMESPACE_EXISTENCE_UNVERIFIED" in codes


def test_pending_pod_is_unresolved_not_placed_on_an_invented_node():
    source = two_node_shop()
    source.pods["shop"].append(pod("web-3", None, app="web", ready=False, phase="Pending"))
    source.deployments["shop"] = [deployment("web", 3), deployment("db", 1)]

    result = discover("shop", source=source)

    assert result.status == "partial"
    assert [u.reason for u in result.unresolved] == ["POD_NOT_SCHEDULED"]
    assert result.unresolved[0].name == "web-3"
    assert result.service_evidence["web"].complete is False
    assert result.service_evidence["db"].complete is True
    placed = {p.node for s in result.graph.services for p in s.pods}
    assert placed == {"worker-1", "worker-2"}  # no "unknown-node"


def test_pod_without_a_matching_deployment_is_reported_not_dropped():
    source = two_node_shop()
    source.pods["shop"].append(pod("batch-1", "worker-2", app="batch"))

    result = discover("shop", source=source)

    assert result.status == "partial"
    assert [(u.kind, u.name, u.reason) for u in result.unresolved] == [
        ("Pod", "batch-1", "NO_MATCHING_DEPLOYMENT")
    ]
    # Unrelated by name: it does not make the other services' evidence incomplete.
    assert result.service_evidence["web"].complete is True


def test_unmatched_pod_owned_by_a_known_deployment_makes_that_service_incomplete():
    source = two_node_shop()
    source.pods["shop"].append(pod("web-9", "worker-1", app="renamed-label", owner="web-5d9c"))

    result = discover("shop", source=source)

    assert result.service_evidence["web"].complete is False
    assert result.service_evidence["db"].complete is True


def test_deployment_with_replicas_but_no_pods_is_unresolved():
    source = two_node_shop()
    source.deployments["shop"].append(deployment("cache", 2))

    result = discover("shop", source=source)

    assert ("Deployment", "cache", "WORKLOAD_HAS_NO_PODS") in [(u.kind, u.name, u.reason) for u in result.unresolved]
    assert result.service_evidence["cache"].complete is False


def test_pod_on_a_control_plane_node_is_unresolved_for_that_pod():
    source = two_node_shop()
    source.pods["shop"].append(pod("web-4", "cp-1", app="web"))

    result = discover("shop", source=source)

    assert ("Pod", "web-4", "POD_ON_EXCLUDED_NODE") in [(u.kind, u.name, u.reason) for u in result.unresolved]
    assert result.service_evidence["web"].complete is False


def test_cluster_with_only_control_plane_nodes_reports_no_worker_nodes():
    source = FakeClusterSource(nodes=[node("cp-1", None, control_plane=True)])

    result = discover("shop", source=source)

    assert "NO_WORKER_NODES" in [w.code for w in result.warnings]
    assert result.graph.nodes == []
    assert result.status == "partial"
    assert not (SAMPLE_NODE_NAMES & {n.name for n in result.graph.nodes})


def test_terminating_pod_is_not_counted_ready():
    source = two_node_shop()
    source.pods["shop"][0] = pod("web-1", "worker-1", app="web", ready=True, terminating=True)

    result = discover("shop", source=source)

    web = next(s for s in result.graph.services if s.name == "web")
    assert web.ready_replicas == 1
    assert next(p for p in web.pods if p.name == "web-1").ready is False


def test_unknown_values_stay_unknown():
    source = FakeClusterSource(
        nodes=[node("worker-1", None, cpu=None, memory=None)],
        pods={"shop": [pod("web-1", "worker-1", app="web", restarts=None)]},
        deployments={"shop": [deployment("web", 1)]},
    )

    graph = discover("shop", source=source).graph

    n = graph.nodes[0]
    assert (n.zone, n.cpu_available, n.memory_available) == (None, None, None)
    assert graph.services[0].pods[0].zone is None
    assert graph.services[0].pods[0].restart_count is None
    assert graph.validation.service_pod_mapping_accuracy is None
    assert graph.validation.pod_node_mapping_accuracy is None
    assert graph.validation.concentration_detection_accuracy is None


def test_observed_restart_count_is_kept_including_zero():
    source = two_node_shop()
    source.pods["shop"][0] = pod("web-1", "worker-1", app="web", restarts=3)

    graph = discover("shop", source=source).graph

    web = next(s for s in graph.services if s.name == "web")
    assert {p.name: p.restart_count for p in web.pods} == {"web-1": 3, "web-2": 0}


def test_namespaces_are_never_mixed():
    source = two_node_shop()
    source.pods["other"] = [pod("web-1", "worker-2", app="web")]
    source.deployments["other"] = [deployment("web", 1)]

    shop = discover("shop", source=source).graph
    other = discover("other", source=source).graph

    assert sum(len(s.pods) for s in shop.services if s.name == "web") == 2
    assert sum(len(s.pods) for s in other.services if s.name == "web") == 1


def test_discovery_only_reads():
    source = two_node_shop()

    discover("shop", source=source)

    assert source.calls == ["nodes", "pods:shop", "deployments:shop"]


def test_envelope_carries_provenance_without_claiming_identity():
    result = discover("shop", source=two_node_shop())

    assert result.captured_at.endswith("Z")
    assert result.cluster.connection_fingerprint == "fake-connection"
    assert result.cluster.identity_verified is False
    assert result.declared_state == "unavailable"


@pytest.mark.parametrize("bad", ["", "   ", "Has Space", "UPPER", "a" * 64])
def test_invalid_namespace_is_rejected_before_any_cluster_call(bad):
    source = two_node_shop()

    with pytest.raises(DiscoveryError) as caught:
        discover(bad, source=source)

    assert caught.value.code == "INVALID_NAMESPACE"
    assert caught.value.http_status == 422
    assert source.calls == []


def test_sample_names_never_leak_when_the_cluster_is_unreachable():
    source = FakeClusterSource(error=ConnectionFailedError("down"))

    with pytest.raises(DiscoveryError) as caught:
        discover("default", source=source)

    text = str(caught.value.detail())
    assert not any(name in text for name in SAMPLE_SERVICE_NAMES | SAMPLE_NODE_NAMES)
