"""The Kubernetes client wrapper: normalisation, error classification, read-only behaviour."""

import time
from types import SimpleNamespace as NS

import pytest
from kubernetes import client, config
from kubernetes.client.rest import ApiException

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


def write_kubeconfig(path, server="https://127.0.0.1:1"):
    path.write_text(
        "apiVersion: v1\nkind: Config\ncurrent-context: c\n"
        f"clusters:\n- name: k\n  cluster:\n    server: {server}\n    insecure-skip-tls-verify: true\n"
        f"users:\n- name: u\n  user:\n    token: {SECRET_TOKEN}\n"
        "contexts:\n- name: c\n  context:\n    cluster: k\n    user: u\n"
    )
    return str(path)


def node_obj(name="w1", labels=None, allocatable=None):
    return NS(metadata=NS(name=name, labels=labels), status=NS(allocatable=allocatable))


def pod_obj(*, node="w1", phase="Running", ready="True", deleting=False, statuses="two", labels=None, owner=True):
    conditions = None if ready is None else [NS(type="Ready", status=ready)]
    if statuses == "two":
        container_statuses = [NS(restart_count=2), NS(restart_count=1)]
    else:
        container_statuses = statuses
    return NS(
        metadata=NS(
            name="p1", labels={"app": "web"} if labels is None else labels,
            deletion_timestamp="2026-01-01T00:00:00Z" if deleting else None,
            owner_references=[NS(kind="ReplicaSet", name="web-abc")] if owner else None,
        ),
        spec=NS(node_name=node),
        status=NS(phase=phase, conditions=conditions, container_statuses=container_statuses),
    )


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
    assert normalize_pod(pod_obj(statuses=[NS(restart_count=0)])).restart_count == 0


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
    assert normalize_deployment(NS(metadata=NS(name="d"), spec=NS(replicas=None))).declared_replicas == 1
    assert normalize_deployment(NS(metadata=NS(name="d"), spec=NS(replicas=0))).declared_replicas == 0


def service_obj(name="reviews", selector=None):
    return NS(metadata=NS(name=name), spec=NS(selector=selector))


def replicaset_obj(name="reviews-v2-76f88c58b8", owners=None):
    return NS(metadata=NS(name=name, owner_references=owners))


def test_service_selector_is_copied():
    selector = {"app": "reviews"}
    result = normalize_service(service_obj(selector=selector))

    assert result == ClusterService(name="reviews", selector={"app": "reviews"})
    assert result.selector is not selector


def test_service_without_selector_has_an_empty_selector():
    assert normalize_service(service_obj(selector=None)).selector == {}


def test_replicaset_resolves_its_deployment_owner():
    rs = normalize_replicaset(replicaset_obj(owners=[NS(kind="Deployment", name="reviews-v2")]))

    assert rs == ClusterReplicaSet(name="reviews-v2-76f88c58b8", deployment_name="reviews-v2")


def test_replicaset_without_owners_has_no_deployment():
    assert normalize_replicaset(replicaset_obj(owners=None)).deployment_name is None


def test_replicaset_owned_by_another_kind_has_no_deployment():
    assert normalize_replicaset(replicaset_obj(owners=[NS(kind="Rollout", name="x")])).deployment_name is None


# ---- connection and errors -----------------------------------------------------------------------

def test_no_kubeconfig_means_not_connected():
    with pytest.raises(NotConnectedError):
        KubernetesApiSource().list_nodes()


def test_unusable_kubeconfig_means_not_connected(tmp_path):
    bad = tmp_path / "bad"
    bad.write_text("this: is: not: a kubeconfig")
    with pytest.raises(NotConnectedError):
        KubernetesApiSource(config_file=str(bad)).list_nodes()


def test_explicit_kubeconfig_is_used_not_the_environment(tmp_path, monkeypatch):
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        raise config.ConfigException("stop here")

    monkeypatch.setattr(config, "new_client_from_config", spy)
    path = write_kubeconfig(tmp_path / "cfg")

    with pytest.raises(NotConnectedError):
        KubernetesApiSource(config_file=path).list_nodes()

    assert seen["config_file"] == path
    assert seen["persist_config"] is False  # never write refreshed credentials back to disk


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, AuthFailedError), (403, ForbiddenError), (500, ApiFailedError), (503, ApiFailedError)],
)
def test_api_status_codes_are_classified(tmp_path, monkeypatch, status, expected):
    def fail(self, *a, **k):
        raise ApiException(status=status, reason="nope")

    monkeypatch.setattr(client.CoreV1Api, "list_node", fail)
    source = KubernetesApiSource(config_file=write_kubeconfig(tmp_path / "cfg"))

    with pytest.raises(expected) as caught:
        source.list_nodes()

    assert SECRET_TOKEN not in str(caught.value)


def test_forbidden_names_the_verb_and_resource_only(tmp_path, monkeypatch):
    monkeypatch.setattr(client.CoreV1Api, "list_namespaced_pod",
                        lambda self, *a, **k: (_ for _ in ()).throw(ApiException(status=403)))
    source = KubernetesApiSource(config_file=write_kubeconfig(tmp_path / "cfg"))

    with pytest.raises(ForbiddenError) as caught:
        source.list_pods("shop")

    assert (caught.value.verb, caught.value.resource) == ("list", "pods")


def test_forbidden_services_and_replicasets_name_their_resource(tmp_path, monkeypatch):
    def deny(self, *a, **k):
        raise ApiException(status=403)

    monkeypatch.setattr(client.CoreV1Api, "list_namespaced_service", deny)
    monkeypatch.setattr(client.AppsV1Api, "list_namespaced_replica_set", deny)
    source = KubernetesApiSource(config_file=write_kubeconfig(tmp_path / "cfg"))

    with pytest.raises(ForbiddenError) as services:
        source.list_services("bookinfo")
    with pytest.raises(ForbiddenError) as replicasets:
        source.list_replicasets("bookinfo")

    assert (services.value.verb, services.value.resource) == ("list", "services")
    assert (replicasets.value.verb, replicasets.value.resource) == ("list", "replicasets")


def test_services_and_replicasets_are_listed_normalised_with_a_timeout(tmp_path, monkeypatch):
    seen = {}

    def services(self, namespace, **k):
        seen["services"] = (namespace, k)
        return NS(items=[service_obj("details", {"app": "details"})])

    def replicasets(self, namespace, **k):
        seen["replicasets"] = (namespace, k)
        return NS(items=[replicaset_obj("details-v1-97999cd5f", [NS(kind="Deployment", name="details-v1")])])

    monkeypatch.setattr(client.CoreV1Api, "list_namespaced_service", services)
    monkeypatch.setattr(client.AppsV1Api, "list_namespaced_replica_set", replicasets)
    source = KubernetesApiSource(config_file=write_kubeconfig(tmp_path / "cfg"), request_timeout=3)

    assert source.list_services("bookinfo") == [ClusterService("details", {"app": "details"})]
    assert source.list_replicasets("bookinfo") == [ClusterReplicaSet("details-v1-97999cd5f", "details-v1")]
    assert seen["services"] == ("bookinfo", {"_request_timeout": 3})
    assert seen["replicasets"] == ("bookinfo", {"_request_timeout": 3})


def test_namespace_without_services_or_replicasets_lists_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(client.CoreV1Api, "list_namespaced_service", lambda self, *a, **k: NS(items=[]))
    monkeypatch.setattr(client.AppsV1Api, "list_namespaced_replica_set", lambda self, *a, **k: NS(items=[]))
    source = KubernetesApiSource(config_file=write_kubeconfig(tmp_path / "cfg"))

    assert source.list_services("empty") == []
    assert source.list_replicasets("empty") == []


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


def test_requests_carry_a_timeout(tmp_path, monkeypatch):
    seen = {}

    def capture(self, *a, **k):
        seen.update(k)
        return NS(items=[])

    monkeypatch.setattr(client.CoreV1Api, "list_node", capture)
    KubernetesApiSource(config_file=write_kubeconfig(tmp_path / "cfg"), request_timeout=3).list_nodes()

    assert seen["_request_timeout"] == 3


def test_source_only_performs_list_calls():
    forbidden = ("create", "delete", "patch", "replace", "connect_")
    public = [n for n in dir(KubernetesApiSource) if not n.startswith("_")]
    assert not [n for n in public if n.startswith(forbidden)]
    assert {"list_nodes", "list_pods", "list_deployments", "list_services", "list_replicasets",
            "connection_fingerprint"} <= set(public)


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


def test_module_exposes_no_load_of_the_global_default_config():
    # The shared global Configuration must not be mutated; each source builds its own ApiClient.
    assert not hasattr(kube, "load_kube_config")
