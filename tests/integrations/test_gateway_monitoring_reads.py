"""The reads that feed the failure timeline: node usage (tolerant of a dead node), Warning events, pod log tails.

The API server is the fake over ``httpx.MockTransport`` with a few extra routes. Nothing here opens a connection, and every read
is a GET: the gateway stays read-only for these.
"""

import httpx
import pytest

from app.engine.recommendation_evaluation_validation.monitoring.readers import gateway_readers
from app.integrations.kubernetes.validation_gateway import KubernetesTestClusterGateway
from tests.support.member4_fake_apiserver import NAMESPACE, FakeApiServer, write_kubeconfig


def build(tmp_path, handler):
    api = FakeApiServer()

    def route(request: httpx.Request) -> httpx.Response:
        answered = handler(request)
        return answered if answered is not None else api(request)
    path = write_kubeconfig(tmp_path / "validation.kubeconfig")
    gateway = KubernetesTestClusterGateway(path, transport=httpx.MockTransport(route))
    return gateway, api


def kubelet_summary(cpu_nano, memory_bytes):
    return httpx.Response(200, json={"node": {"cpu": {"usageNanoCores": cpu_nano}, "memory": {"workingSetBytes": memory_bytes}}})


def test_node_usage_falls_back_to_the_kubelet_and_leaves_a_dead_node_unknown(tmp_path):
    def handler(request):
        path = request.url.path
        if path == "/apis/metrics.k8s.io/v1beta1/nodes":
            return httpx.Response(404, json={"kind": "Status"})
        if path.endswith("/proxy/stats/summary"):
            node = path.split("/")[-4]
            return httpx.Response(502, json={"kind": "Status"}) if node == "w1" else kubelet_summary(250_000_000, 536_870_912)
        return None
    gateway, api = build(tmp_path, handler)

    usage = gateway.read_node_usage()

    assert usage["source"] == "kubelet-summary"
    assert usage["cpu"]["w1"] is None and usage["mem"]["w1"] is None            # the dead node's kubelet cannot answer: unknown, not 0
    assert usage["cpu"]["w2"] == pytest.approx(250.0) and usage["mem"]["w2"] == pytest.approx(512.0)
    assert api.methods == {"GET"}


def test_node_usage_prefers_the_metrics_api_when_it_exists(tmp_path):
    def handler(request):
        if request.url.path == "/apis/metrics.k8s.io/v1beta1/nodes":
            return httpx.Response(200, json={"items": [{"metadata": {"name": "w1"}, "usage": {"cpu": "120m", "memory": "1Gi"}}]})
        return None
    gateway, _ = build(tmp_path, handler)

    usage = gateway.read_node_usage()

    assert usage == {"cpu": {"w1": 120.0}, "mem": {"w1": 1024.0}, "source": "metrics.k8s.io"}


def test_warning_events_keep_this_namespace_and_nodes_and_drop_the_rest(tmp_path):
    events = {"items": [
        {"metadata": {"uid": "1"}, "involvedObject": {"kind": "Node", "name": "w1"}, "reason": "NodeNotReady", "message": "Node w1 status is now: NodeNotReady",
         "lastTimestamp": "2026-11-19T10:00:05Z", "count": 1},
        {"metadata": {"uid": "2"}, "involvedObject": {"kind": "Pod", "name": "reviews-1", "namespace": NAMESPACE}, "reason": "FailedScheduling",
         "message": "0/3 nodes are available", "lastTimestamp": "2026-11-19T10:00:09Z", "count": 3},
        {"metadata": {"uid": "3"}, "involvedObject": {"kind": "Pod", "name": "other", "namespace": "elsewhere"}, "reason": "BackOff",
         "lastTimestamp": "2026-11-19T10:00:01Z"}]}

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=events) if request.url.path == "/api/v1/events" else None
    gateway, api = build(tmp_path, handler)

    found = gateway.read_warning_events(NAMESPACE)

    assert [e["reason"] for e in found] == ["NodeNotReady", "FailedScheduling"]
    assert found[1]["object"] == "Pod/reviews-1" and found[1]["count"] == 3
    assert "fieldSelector=type%3DWarning" in str(next(r for r in seen if r.url.path == "/api/v1/events").url)


def test_a_pod_log_tail_is_plain_text_and_an_unreachable_kubelet_raises(tmp_path):
    def handler(request):
        if request.url.path.endswith("/pods/reviews-1/log"):
            assert request.url.params["tailLines"] == "3"
            return httpx.Response(200, text="a\nb\nc\nd\n")
        if request.url.path.endswith("/pods/stranded/log"):
            return httpx.Response(502, text="no route to the node")
        return None
    gateway, _ = build(tmp_path, handler)

    assert gateway.read_pod_log_tail(NAMESPACE, "reviews-1", lines=3) == ["b", "c", "d"]
    with pytest.raises(Exception):
        gateway.read_pod_log_tail(NAMESPACE, "stranded")


def test_the_real_readers_report_ready_pods_per_service_and_node_readiness(tmp_path):
    gateway, _ = build(tmp_path, lambda request: None)
    from app.engine.deployment_topology_modeling.discovery import discover
    gateway._reader = discover

    readers = gateway_readers(gateway, NAMESPACE)
    services, nodes = readers.services(), readers.nodes()

    assert services and all(isinstance(v, int) and v >= 0 for v in services.values())
    assert nodes and all(v in (True, False, None) for v in nodes.values())
    assert readers.request is None      # no workflow check, so no requests are made
