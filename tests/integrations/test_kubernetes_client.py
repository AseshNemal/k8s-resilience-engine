"""The Kubernetes client wrapper: normalisation, error classification, read-only behaviour.

The API server is a ``httpx.MockTransport``, so these tests exercise the real kubeconfig loader, request
paths, headers and error mapping without a cluster or a network.
"""

import base64
import json
import time
from pathlib import Path

import httpx
import pytest

from app.integrations.kubernetes import client as kube
from app.integrations.kubernetes.client import (
    ApiFailedError,
    AuthFailedError,
    ConnectionFailedError,
    ForbiddenError,
    KubernetesApiSource,
    NotConnectedError,
    normalize_deployment,
    normalize_node,
    normalize_pod,
    normalize_replicaset,
    normalize_service,
)
from app.integrations.kubernetes.models import ClusterReplicaSet, ClusterService

SECRET_TOKEN = "tok-3f9a-super-secret-value"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tls"
CA_B64 = base64.b64encode((FIXTURES / "ca.pem").read_bytes()).decode()


def write_kubeconfig(path, server="https://127.0.0.1:1", user="token: " + SECRET_TOKEN, cluster_extra=""):
    path.write_text(
        "apiVersion: v1\nkind: Config\ncurrent-context: c\n"
        f"clusters:\n- name: k\n  cluster:\n    server: {server}\n    certificate-authority-data: {CA_B64}\n{cluster_extra}"
        f"users:\n- name: u\n  user:\n    {user}\n"
        "contexts:\n- name: c\n  context:\n    cluster: k\n    user: u\n"
    )
    return str(path)


class FakeApi:
    """Answers GETs the way the API server does and records every request."""

    def __init__(self, routes=None, status=200):
        self.routes = routes or {}
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"kind": "Status", "reason": "nope"})
        body = self.routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"kind": "Status", "reason": "NotFound"})
        return httpx.Response(200, json=body)

    def source(self, tmp_path, **kwargs):
        return KubernetesApiSource(
            config_file=write_kubeconfig(tmp_path / "cfg"), transport=httpx.MockTransport(self), **kwargs
        )


def items(*objects):
    return {"kind": "List", "items": list(objects)}


def node_obj(name="w1", labels=None, allocatable=None):
    obj = {"metadata": {"name": name}, "status": {}}
    if labels is not None:
        obj["metadata"]["labels"] = labels
    if allocatable is not None:
        obj["status"]["allocatable"] = allocatable
    return obj


def pod_obj(*, node="w1", phase="Running", ready="True", deleting=False, statuses="two", labels=None, owner=True):
    conditions = None if ready is None else [{"type": "Ready", "status": ready}]
    if statuses == "two":
        container_statuses = [{"restartCount": 2}, {"restartCount": 1}]
    else:
        container_statuses = statuses
    meta = {"name": "p1", "labels": {"app": "web"} if labels is None else labels}
    if deleting:
        meta["deletionTimestamp"] = "2026-01-01T00:00:00Z"
    if owner:
        meta["ownerReferences"] = [{"kind": "ReplicaSet", "name": "web-abc"}]
    status = {"phase": phase}
    if conditions is not None:
        status["conditions"] = conditions
    if container_statuses is not None:
        status["containerStatuses"] = container_statuses
    spec = {} if node is None else {"nodeName": node}
    return {"metadata": meta, "spec": spec, "status": status}


# ---- normalisation -------------------------------------------------------------------------------

def test_node_zone_and_capacity_missing_stay_none():
    n = normalize_node(node_obj(labels=None, allocatable=None))
    assert (n.zone, n.cpu_available, n.memory_available, n.control_plane) == (None, None, None, False)


def test_node_values_and_control_plane_detection():
    n = normalize_node(node_obj(
        labels={"topology.kubernetes.io/zone": "z1", "node-role.kubernetes.io/control-plane": ""},
        allocatable={"cpu": "2", "memory": "4Gi"},
    ))
    assert (n.zone, n.cpu_available, n.memory_available, n.control_plane) == ("z1", "2", "4Gi", True)


def test_pod_restart_count_sums_regular_container_statuses():
    assert normalize_pod(pod_obj()).restart_count == 3


def test_pod_restart_count_is_unknown_without_container_statuses():
    assert normalize_pod(pod_obj(statuses=None)).restart_count is None


def test_pod_restart_count_of_zero_is_an_observed_zero():
    assert normalize_pod(pod_obj(statuses=[{"restartCount": 0}])).restart_count == 0


def test_a_container_status_without_a_restart_count_counts_as_zero_like_the_api_omitting_a_zero():
    assert normalize_pod(pod_obj(statuses=[{"name": "app"}, {"restartCount": 4}])).restart_count == 4


def test_terminating_pod_is_never_ready():
    p = normalize_pod(pod_obj(ready="True", deleting=True))
    assert (p.terminating, p.ready) == (True, False)


def test_pod_without_ready_condition_is_not_ready():
    assert normalize_pod(pod_obj(ready=None)).ready is False


def test_unscheduled_pod_has_no_node():
    p = normalize_pod(pod_obj(node=None, phase="Pending", ready="False"))
    assert (p.node, p.phase) == (None, "Pending")


def test_pod_owner_and_app_label():
    p = normalize_pod(pod_obj())
    assert (p.owner_kind, p.owner_name, p.app_label) == ("ReplicaSet", "web-abc", "web")
    q = normalize_pod(pod_obj(labels={}, owner=False))
    assert (q.owner_kind, q.owner_name, q.app_label) == (None, None, "")


def test_deployment_declared_replicas_default_matches_kubernetes_semantics():
    assert normalize_deployment({"metadata": {"name": "d"}, "spec": {}}).declared_replicas == 1
    assert normalize_deployment({"metadata": {"name": "d"}, "spec": {"replicas": None}}).declared_replicas == 1
    assert normalize_deployment({"metadata": {"name": "d"}, "spec": {"replicas": 0}}).declared_replicas == 0


def service_obj(name="reviews", selector=None):
    spec = {} if selector is None else {"selector": selector}
    return {"metadata": {"name": name}, "spec": spec}


def replicaset_obj(name="reviews-v2-76f88c58b8", owners=None):
    meta = {"name": name}
    if owners is not None:
        meta["ownerReferences"] = owners
    return {"metadata": meta}


def test_service_selector_is_copied():
    selector = {"app": "reviews"}
    result = normalize_service(service_obj(selector=selector))

    assert result == ClusterService(name="reviews", selector={"app": "reviews"})
    assert result.selector is not selector


def test_service_without_selector_has_an_empty_selector():
    assert normalize_service(service_obj(selector=None)).selector == {}


def test_replicaset_resolves_its_deployment_owner():
    rs = normalize_replicaset(replicaset_obj(owners=[{"kind": "Deployment", "name": "reviews-v2"}]))

    assert rs == ClusterReplicaSet(name="reviews-v2-76f88c58b8", deployment_name="reviews-v2")


def test_replicaset_without_owners_has_no_deployment():
    assert normalize_replicaset(replicaset_obj(owners=None)).deployment_name is None


def test_replicaset_owned_by_another_kind_has_no_deployment():
    assert normalize_replicaset(replicaset_obj(owners=[{"kind": "Rollout", "name": "x"}])).deployment_name is None


# ---- connection and errors -----------------------------------------------------------------------

def test_no_kubeconfig_means_not_connected():
    with pytest.raises(NotConnectedError):
        KubernetesApiSource().list_nodes()


def test_unusable_kubeconfig_means_not_connected(tmp_path):
    bad = tmp_path / "bad"
    bad.write_text("this: is: not: a kubeconfig")
    with pytest.raises(NotConnectedError):
        KubernetesApiSource(config_file=str(bad)).list_nodes()


def test_a_kubeconfig_with_no_current_context_is_not_usable(tmp_path):
    path = tmp_path / "cfg"
    path.write_text("apiVersion: v1\nkind: Config\nclusters: []\ncontexts: []\n")
    with pytest.raises(NotConnectedError, match="no current context"):
        KubernetesApiSource(config_file=str(path)).list_nodes()


def test_explicit_kubeconfig_is_used_not_the_environment(tmp_path, monkeypatch):
    explicit = write_kubeconfig(tmp_path / "explicit", server="https://explicit.example:6443")
    other = write_kubeconfig(tmp_path / "other", server="https://environment.example:6443")
    monkeypatch.setenv("KUBECONFIG", other)
    api = FakeApi({"/api/v1/nodes": items()})

    KubernetesApiSource(config_file=explicit, transport=httpx.MockTransport(api)).list_nodes()

    assert api.requests[0].url.host == "explicit.example"


def test_the_environment_is_used_when_no_file_is_given(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", write_kubeconfig(tmp_path / "env", server="https://environment.example:6443"))
    api = FakeApi({"/api/v1/nodes": items()})

    KubernetesApiSource(transport=httpx.MockTransport(api)).list_nodes()

    assert api.requests[0].url.host == "environment.example"


def test_a_refreshed_credential_is_never_written_back(tmp_path):
    path = write_kubeconfig(tmp_path / "cfg")
    before = Path(path).read_bytes()
    FakeApi({"/api/v1/nodes": items()}).source(tmp_path).list_nodes()
    assert Path(path).read_bytes() == before


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, AuthFailedError), (403, ForbiddenError), (500, ApiFailedError), (503, ApiFailedError), (302, ApiFailedError)],
)
def test_api_status_codes_are_classified(tmp_path, status, expected):
    source = FakeApi(status=status).source(tmp_path)

    with pytest.raises(expected) as caught:
        source.list_nodes()

    assert SECRET_TOKEN not in str(caught.value)
    if expected is ApiFailedError:
        assert caught.value.status == status


def test_forbidden_names_the_verb_and_resource_only(tmp_path):
    with pytest.raises(ForbiddenError) as caught:
        FakeApi(status=403).source(tmp_path).list_pods("shop")

    assert (caught.value.verb, caught.value.resource) == ("list", "pods")


def test_forbidden_services_and_replicasets_name_their_resource(tmp_path):
    source = FakeApi(status=403).source(tmp_path)

    with pytest.raises(ForbiddenError) as services:
        source.list_services("bookinfo")
    with pytest.raises(ForbiddenError) as replicasets:
        source.list_replicasets("bookinfo")

    assert (services.value.verb, services.value.resource) == ("list", "services")
    assert (replicasets.value.verb, replicasets.value.resource) == ("list", "replicasets")


def test_every_collection_is_read_from_its_own_path_normalised_with_a_timeout(tmp_path):
    api = FakeApi({
        "/api/v1/nodes": items(node_obj("w1")),
        "/api/v1/namespaces/shop/pods": items(pod_obj()),
        "/apis/apps/v1/namespaces/shop/deployments": items({"metadata": {"name": "web"}, "spec": {"replicas": 3}}),
        "/api/v1/namespaces/shop/services": items(service_obj("details", {"app": "details"})),
        "/apis/apps/v1/namespaces/shop/replicasets":
            items(replicaset_obj("details-v1-97999cd5f", [{"kind": "Deployment", "name": "details-v1"}])),
    })
    source = api.source(tmp_path, request_timeout=3)

    assert [n.name for n in source.list_nodes()] == ["w1"]
    assert [p.owner_name for p in source.list_pods("shop")] == ["web-abc"]
    assert [d.declared_replicas for d in source.list_deployments("shop")] == [3]
    assert source.list_services("shop") == [ClusterService("details", {"app": "details"})]
    assert source.list_replicasets("shop") == [ClusterReplicaSet("details-v1-97999cd5f", "details-v1")]

    assert [r.url.path for r in api.requests] == list(api.routes)
    for request in api.requests:
        assert request.extensions["timeout"] == {"connect": 3, "read": 3, "write": 3, "pool": 3}


def test_namespace_without_services_or_replicasets_lists_empty(tmp_path):
    api = FakeApi({
        "/api/v1/namespaces/empty/services": items(),
        "/apis/apps/v1/namespaces/empty/replicasets": items(),
    })
    source = api.source(tmp_path)

    assert source.list_services("empty") == []
    assert source.list_replicasets("empty") == []


def test_a_namespace_name_cannot_change_the_request_path(tmp_path):
    api = FakeApi({})
    with pytest.raises(ApiFailedError):
        api.source(tmp_path).list_pods("a/../../nodes?x=y")

    assert api.requests[0].url.raw_path.decode().startswith("/api/v1/namespaces/a%2F..%2F..%2Fnodes%3Fx%3Dy/pods")


def test_a_reply_without_an_items_list_is_an_api_failure_not_an_empty_cluster(tmp_path):
    for body in ({"kind": "Status"}, {"items": None}, [], "text"):
        with pytest.raises(ApiFailedError):
            FakeApi({"/api/v1/nodes": body}).source(tmp_path).list_nodes()


def test_a_reply_that_is_not_json_is_a_connection_failure(tmp_path):
    def html(request):
        return httpx.Response(200, text="<html>captive portal</html>")

    source = KubernetesApiSource(config_file=write_kubeconfig(tmp_path / "cfg"), transport=httpx.MockTransport(html))
    with pytest.raises(ConnectionFailedError, match="InvalidResponse"):
        source.list_nodes()


def test_unreachable_api_server_is_a_connection_failure_and_leaks_no_token(tmp_path):
    source = KubernetesApiSource(
        config_file=write_kubeconfig(tmp_path / "cfg", server="https://127.0.0.1:1"),
        request_timeout=2,
    )
    started = time.monotonic()

    with pytest.raises(ConnectionFailedError) as caught:
        source.list_nodes()

    assert time.monotonic() - started < 15
    assert SECRET_TOKEN not in str(caught.value)


def test_a_transport_failure_never_carries_the_token(tmp_path):
    def refuse(request):
        raise httpx.ConnectError(f"cannot connect with {SECRET_TOKEN}")

    source = KubernetesApiSource(config_file=write_kubeconfig(tmp_path / "cfg"), transport=httpx.MockTransport(refuse))
    with pytest.raises(ConnectionFailedError) as caught:
        source.list_nodes()

    assert SECRET_TOKEN not in str(caught.value) and str(caught.value) == "ConnectError"


def test_the_bearer_token_is_sent_and_a_client_certificate_only_user_sends_none(tmp_path):
    api = FakeApi({"/api/v1/nodes": items()})
    api.source(tmp_path).list_nodes()
    assert api.requests[0].headers["authorization"] == f"Bearer {SECRET_TOKEN}"

    anonymous = FakeApi({"/api/v1/nodes": items()})
    path = write_kubeconfig(tmp_path / "anon", user="username: ''")
    KubernetesApiSource(config_file=path, transport=httpx.MockTransport(anonymous)).list_nodes()
    assert "authorization" not in anonymous.requests[0].headers


def test_the_ambient_environment_cannot_change_how_the_cluster_is_reached(tmp_path, monkeypatch):
    """No proxy variable, ~/.netrc or CA bundle variable may influence a credentialed request."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "not-a-bundle"))
    source = KubernetesApiSource(config_file=write_kubeconfig(tmp_path / "cfg", server="https://127.0.0.1:1"), request_timeout=2)

    with pytest.raises(ConnectionFailedError):  # still a plain connection refusal, not a proxy error
        source.list_nodes()


@pytest.mark.parametrize("user", [
    "exec:\n      apiVersion: client.authentication.k8s.io/v1\n      command: /bin/true",
    "auth-provider:\n      name: gcp",
])
def test_a_credential_plugin_is_refused_and_never_run(tmp_path, user):
    path = write_kubeconfig(tmp_path / "cfg", user=user)
    with pytest.raises(NotConnectedError, match="credential plugin"):
        KubernetesApiSource(config_file=path).list_nodes()


def test_insecure_skip_tls_verify_and_proxies_are_refused(tmp_path):
    for extra, word in (("    insecure-skip-tls-verify: true\n", "insecure-skip-tls-verify"), ("    proxy-url: http://p:1\n", "proxy-url")):
        path = write_kubeconfig(tmp_path / "cfg", cluster_extra=extra)
        with pytest.raises(NotConnectedError, match=word):
            KubernetesApiSource(config_file=path).list_nodes()


def test_source_only_performs_list_calls(tmp_path):
    forbidden = ("create", "delete", "patch", "replace", "connect_", "post", "put")
    public = [n for n in dir(KubernetesApiSource) if not n.startswith("_")]
    assert not [n for n in public if n.startswith(forbidden)]
    assert {"list_nodes", "list_pods", "list_deployments", "list_services", "list_replicasets",
            "list_workload_namespaces", "connection_fingerprint"} <= set(public)

    api = FakeApi({
        "/api/v1/nodes": items(), "/api/v1/pods": items(), "/apis/apps/v1/deployments": items(),
        "/api/v1/namespaces/n/pods": items(), "/apis/apps/v1/namespaces/n/deployments": items(),
        "/api/v1/namespaces/n/services": items(), "/apis/apps/v1/namespaces/n/replicasets": items(),
    })
    source = api.source(tmp_path)
    source.list_nodes(), source.list_pods("n"), source.list_deployments("n"), source.list_services("n")
    source.list_replicasets("n"), source.list_workload_namespaces()
    assert {r.method for r in api.requests} == {"GET"}


# ---- connection fingerprint ----------------------------------------------------------------------

def test_fingerprint_is_none_when_not_connected():
    assert KubernetesApiSource().connection_fingerprint() is None


def test_fingerprint_changes_with_the_cluster_but_not_with_a_renewed_token(tmp_path):
    a = write_kubeconfig(tmp_path / "a", server="https://10.0.0.1:6443")
    b = write_kubeconfig(tmp_path / "b", server="https://10.0.0.2:6443")
    (tmp_path / "a2").write_text((tmp_path / "a").read_text().replace(SECRET_TOKEN, "renewed-token"))

    fa = KubernetesApiSource(config_file=a).connection_fingerprint()
    fb = KubernetesApiSource(config_file=b).connection_fingerprint()
    fa2 = KubernetesApiSource(config_file=str(tmp_path / "a2")).connection_fingerprint()

    assert fa and fb and fa != fb
    assert fa == fa2
    assert SECRET_TOKEN not in fa


def test_fingerprint_is_the_documented_hash_of_server_and_the_ca_as_written(tmp_path):
    """Stored state is keyed by this value, so its definition must not drift between versions."""
    import hashlib

    path = write_kubeconfig(tmp_path / "cfg", server="https://10.0.0.1:6443")
    expected = hashlib.sha256(f"https://10.0.0.1:6443\0{CA_B64}".encode()).hexdigest()[:32]
    assert KubernetesApiSource(config_file=path).connection_fingerprint() == expected


def test_fingerprint_uses_the_ca_path_as_written_not_the_file_contents(tmp_path):
    import hashlib

    ca = tmp_path / "ca.crt"
    ca.write_bytes((FIXTURES / "ca.pem").read_bytes())
    cfg = tmp_path / "cfg"
    cfg.write_text(
        "current-context: c\nclusters:\n- name: k\n  cluster: {server: 'https://h:1', certificate-authority: ca.crt}\n"
        "users:\n- name: u\n  user: {token: t}\ncontexts:\n- name: c\n  context: {cluster: k, user: u}\n"
    )
    expected = hashlib.sha256(b"https://h:1\0ca.crt").hexdigest()[:32]
    assert KubernetesApiSource(config_file=str(cfg)).connection_fingerprint() == expected


def test_a_context_whose_credential_cannot_be_used_still_has_an_identity(tmp_path):
    path = write_kubeconfig(tmp_path / "cfg", user="exec:\n      command: /bin/true")
    assert KubernetesApiSource(config_file=path).connection_fingerprint()


def test_fingerprint_is_none_without_a_server(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.write_text(
        "current-context: c\nclusters:\n- name: k\n  cluster: {}\nusers:\n- name: u\n  user: {}\n"
        "contexts:\n- name: c\n  context: {cluster: k, user: u}\n"
    )
    assert KubernetesApiSource(config_file=str(cfg)).connection_fingerprint() is None


def test_module_keeps_no_global_state_for_the_cluster_it_talks_to():
    # Each source reads its own kubeconfig; switching clusters cannot leave a stale client behind.
    assert not hasattr(kube, "load_kube_config") and not hasattr(kube, "_default_client")


# ---- namespaces that hold workloads --------------------------------------------------------------

def ns_obj(namespace):
    return {"metadata": {"namespace": namespace, "name": "x"}}


def test_workload_namespaces_are_the_distinct_sorted_namespaces_of_pods_and_deployments(tmp_path):
    api = FakeApi({
        "/api/v1/pods": items(ns_obj("shop"), ns_obj("bookinfo"), ns_obj("bookinfo")),
        "/apis/apps/v1/deployments": items(ns_obj("resilience-test"), ns_obj("shop")),
    })

    assert api.source(tmp_path).list_workload_namespaces() == ["bookinfo", "resilience-test", "shop"]


def test_workload_namespaces_of_an_empty_cluster_is_empty(tmp_path):
    api = FakeApi({"/api/v1/pods": items(), "/apis/apps/v1/deployments": items()})

    assert api.source(tmp_path).list_workload_namespaces() == []


def test_an_object_without_a_namespace_is_skipped_not_invented_or_crashing(tmp_path):
    api = FakeApi({
        "/api/v1/pods": items({"metadata": {"name": "odd"}}, ns_obj("shop")),
        "/apis/apps/v1/deployments": items(),
    })

    assert api.source(tmp_path).list_workload_namespaces() == ["shop"]


def test_workload_namespace_reads_carry_a_timeout(tmp_path):
    api = FakeApi({"/api/v1/pods": items(), "/apis/apps/v1/deployments": items()})

    api.source(tmp_path, request_timeout=3).list_workload_namespaces()

    assert [r.url.path for r in api.requests] == ["/api/v1/pods", "/apis/apps/v1/deployments"]
    assert all(r.extensions["timeout"]["read"] == 3 for r in api.requests)


@pytest.mark.parametrize(("denied", "resource"), [("/api/v1/pods", "pods"), ("/apis/apps/v1/deployments", "deployments")])
def test_forbidden_workload_namespace_reads_name_their_resource(tmp_path, denied, resource):
    def handler(request):
        if request.url.path == denied:
            return httpx.Response(403, json={"kind": "Status", "reason": "Forbidden"})
        return httpx.Response(200, json=items())

    source = KubernetesApiSource(config_file=write_kubeconfig(tmp_path / "cfg"), transport=httpx.MockTransport(handler))
    with pytest.raises(ForbiddenError) as caught:
        source.list_workload_namespaces()

    assert (caught.value.verb, caught.value.resource) == ("list", resource)


def test_request_bodies_are_never_sent(tmp_path):
    api = FakeApi({"/api/v1/nodes": items()})
    api.source(tmp_path).list_nodes()
    assert api.requests[0].content == b"" and json.dumps(dict(api.requests[0].headers)).count("content-type") == 0
