"""The disposable test cluster: creating a copy only inside the whitelist, evaluating on its own topology, and the three routes."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import validation_loop_environment as environment_module
from app.api.validation_loop_disposable import get_docker_facts, get_source_gateway, get_target_gateway
from app.api.validation_loop_environment import target_loader
from app.engine.deployment_topology_modeling.discovery import discover
from app.engine.recommendation_evaluation_validation.approval import has_verified_provenance
from app.engine.recommendation_evaluation_validation.validation_loop.models import VALIDATION_TARGET_SOURCE
from app.integrations.kubernetes.client import ForbiddenError
from app.integrations.kubernetes.validation_gateway import KubernetesTestClusterGateway, WritesDisabledError
from app.main import app
from tests.support.member4_fake_apiserver import write_kubeconfig
from tests.support.member4_fake_world import FakeWorld
from tests.support.member4_harness import bookinfo_workflow

BASE = "/api/recommendations/validation-loop/disposable"
GIB = 1024**3


def recording_gateway(tmp_path, *, status=201, **options):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(status, json={})

    gateway = KubernetesTestClusterGateway(write_kubeconfig(tmp_path / "t.kubeconfig"), transport=httpx.MockTransport(handler), **options)
    return gateway, seen


def deployment(namespace="app"):
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": namespace}, "spec": {}}


# --- the gateway may create only the copy's kinds, only in the copy's namespace ----------------------------------------------------

def test_a_deployment_is_created_in_its_collection_and_a_conflict_means_it_already_existed(tmp_path):
    gateway, seen = recording_gateway(tmp_path, allow_writes=True)

    assert gateway.create_object(deployment(), namespace="app") is True
    assert seen == [("POST", "/apis/apps/v1/namespaces/app/deployments", deployment())]
    conflicted, _ = recording_gateway(tmp_path, status=409, allow_writes=True)
    assert conflicted.create_object(deployment(), namespace="app") is False


@pytest.mark.parametrize("document, namespace", [
    ({"kind": "Secret", "metadata": {"name": "s", "namespace": "app"}}, "app"),
    ({"kind": "Namespace", "metadata": {"name": "x", "namespace": "app"}}, "app"),
    ({"kind": "ClusterRole", "metadata": {"name": "x", "namespace": "app"}}, "app"),
    ({"kind": "Pod", "metadata": {"name": "x", "namespace": "app"}}, "app"),
    ({"kind": "Deployment", "metadata": {"name": "x", "namespace": "elsewhere"}}, "app"),
    ({"kind": "Deployment", "metadata": {"namespace": "app"}}, "app"),
    ({"kind": "Deployment", "metadata": {"name": "x", "namespace": "kube-system"}}, "kube-system"),
    ({"kind": "Deployment", "metadata": {"name": "x", "namespace": "default"}}, "default"),
])
def test_anything_outside_the_whitelist_is_refused_before_a_request_is_made(tmp_path, document, namespace):
    gateway, seen = recording_gateway(tmp_path, allow_writes=True)

    with pytest.raises(ForbiddenError):
        gateway.create_object(document, namespace=namespace)
    assert seen == []


def test_a_gateway_built_read_only_creates_nothing(tmp_path):
    gateway, seen = recording_gateway(tmp_path)

    with pytest.raises(WritesDisabledError):
        gateway.create_object(deployment(), namespace="app")
    with pytest.raises(WritesDisabledError):
        gateway.ensure_namespace("app")
    assert seen == []


def test_the_namespace_is_created_with_a_label_that_marks_it_as_a_test_namespace_and_system_ones_never(tmp_path):
    gateway, seen = recording_gateway(tmp_path, allow_writes=True)

    assert gateway.ensure_namespace("app") is True
    assert seen[0][1] == "/api/v1/namespaces" and seen[0][2]["metadata"]["labels"] == {"resilience-validation/test-namespace": "true"}
    for name in ("kube-system", "default", "kube-public"):
        with pytest.raises(ForbiddenError):
            gateway.ensure_namespace(name)


def test_secrets_cannot_be_listed_through_the_raw_read(tmp_path):
    gateway, seen = recording_gateway(tmp_path)

    with pytest.raises(ForbiddenError):
        gateway.list_raw("secrets", "/api/v1/namespaces/app/secrets")
    assert seen == []


# --- the recommendation is computed on the target's own topology ---------------------------------------------------------------------

def test_the_target_loader_evaluates_on_the_targets_topology_and_the_result_can_never_be_approved(tmp_path, monkeypatch):
    world = FakeWorld(tmp_path)
    monkeypatch.setattr(environment_module, "get_runtime_component_inputs", lambda workflow_id, namespace: SimpleNamespace(workflow=bookinfo_workflow()))
    gateway = KubernetesTestClusterGateway(world.kubeconfig, topology_reader=discover, transport=world.api.transport)

    loaded = target_loader("FROZEN")("productpage-view", "offline-benchmark", gateway)

    evidence = loaded.live.evidence
    assert evidence["source"] == VALIDATION_TARGET_SOURCE
    assert evidence["schedulingFacts"]["intentAdapter"]["origin"] == "member4-fallback"
    assert evidence["schedulingFacts"]["intentAdapter"]["servicesWithIntent"]
    approval = SimpleNamespace(evaluation_snapshot={"provenance": {"source": evidence["source"]}})
    assert has_verified_provenance(approval) is False
    assert {s.name for s in loaded.graph.services} >= {"productpage", "reviews"} and loaded.workflow_basis == "FROZEN"
    assert any("test cluster's own topology" in note for note in loaded.notes)


# --- the routes ---------------------------------------------------------------------------------------------------------------------

class FakeFacts:
    def __init__(self, memory_gib, containers, *, broken=False):
        self.memory, self.containers, self.broken = memory_gib, containers, broken

    def info(self):
        if self.broken:
            raise httpx.ConnectError("no proxy")
        return {"memTotalBytes": int(self.memory * GIB), "ncpu": 5}

    def container_memory(self):
        return {k: int(v * GIB) for k, v in self.containers.items()}


NODES = {"desktop-control-plane": 0.8, "desktop-worker": 0.55, "desktop-worker2": 0.55, "other": 0.9}


class FakeSide:
    """A gateway double for the route tests: raw lists to read, objects to create, an identity."""

    def __init__(self, uid, objects=None):
        self.uid, self.objects, self.created, self.namespaces = uid, objects or {}, [], []

    def identity(self):
        return SimpleNamespace(cluster_uid=self.uid)

    def list_raw(self, resource, path):
        return self.objects.get(resource, [])

    def ensure_namespace(self, name):
        self.namespaces.append(name)
        return True

    def create_object(self, document, *, namespace):
        key = (document["kind"], document["metadata"]["name"])
        if key in {c[0] for c in self.created}:
            return False
        self.created.append((key, namespace))
        return True


def web():
    return {"metadata": {"name": "web", "namespace": "app", "uid": "u"}, "spec": {"replicas": 2, "selector": {"matchLabels": {"app": "web"}},
            "template": {"metadata": {"labels": {"app": "web"}}, "spec": {"containers": [{"name": "c", "image": "i"}]}}}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RESILIENCE_ALLOW_CLUSTER_WRITES", "1")
    yield TestClient(app)
    for dependency in (get_docker_facts, get_source_gateway, get_target_gateway):
        app.dependency_overrides.pop(dependency, None)


def use(facts=None, source=None, target=None):
    if facts is not None:
        app.dependency_overrides[get_docker_facts] = lambda: facts
    if source is not None:
        app.dependency_overrides[get_source_gateway] = lambda: (lambda: source)
    if target is not None:
        app.dependency_overrides[get_target_gateway] = lambda: (lambda: target)


def test_a_machine_that_cannot_hold_the_nodes_is_told_what_to_change_and_nothing_is_offered(client):
    use(facts=FakeFacts(3.8, NODES))

    body = client.get(f"{BASE}/assessment").json()

    assert body["verdict"] == "INSUFFICIENT" and body["canCreate"] is False
    assert any("Raise Docker Desktop's memory" in line for line in body["advice"])


def test_only_a_sufficient_assessment_says_a_cluster_can_be_created(client):
    use(facts=FakeFacts(12, {"desktop-control-plane": 0.8, "desktop-worker": 0.55}))

    uncertain = client.get(f"{BASE}/assessment").json()
    sufficient = client.get(f"{BASE}/assessment", params={"appMemoryMiB": 300}).json()

    assert uncertain["verdict"] == "UNCERTAIN" and uncertain["canCreate"] is False and uncertain["advice"]
    assert sufficient["verdict"] == "SUFFICIENT" and sufficient["canCreate"] is True and sufficient["advice"] == []


def test_docker_that_cannot_be_read_is_unknown_with_a_reason_not_a_server_error(client):
    use(facts=FakeFacts(0, {}, broken=True))

    answer = client.get(f"{BASE}/assessment")

    assert answer.status_code == 200 and answer.json()["verdict"] == "UNKNOWN" and answer.json()["canCreate"] is False


def test_an_unknown_profile_is_a_422(client):
    use(facts=FakeFacts(12, {}))

    assert client.get(f"{BASE}/assessment", params={"profile": "huge"}).status_code == 422


def test_the_preview_lists_what_would_be_copied_and_writes_nothing(client):
    source = FakeSide("uid-source", {"deployments": [web()], "services": [{"metadata": {"name": "web", "namespace": "app"}, "spec": {"selector": {"app": "web"}, "ports": [{"port": 80}]}}]})
    use(source=source)

    body = client.post(f"{BASE}/preview", json={"namespace": "app"}).json()

    assert body["deployable"] is True and {o["name"] for o in body["objects"]} == {"web"} and body["targetNamespace"] == "resilience-validation-app"
    assert body["previewDigest"] and "kind: Deployment" in body["yaml"] and source.created == []


def test_a_preview_names_what_could_not_be_copied(client):
    pod = {"containers": [{"name": "c", "envFrom": [{"secretRef": {"name": "db"}}]}]}
    item = web()
    item["spec"]["template"]["spec"] = pod
    use(source=FakeSide("uid-source", {"deployments": [item]}))

    body = client.post(f"{BASE}/preview", json={"namespace": "app"}).json()

    assert body["deployable"] is False and body["missing"][0]["kind"] == "Secret" and "yourself" in body["missing"][0]["why"]


def test_pasted_manifests_are_the_fallback_and_a_pasted_secret_is_a_422(client):
    use(source=FakeSide("uid-source"))

    ok = client.post(f"{BASE}/preview", json={"namespace": "app", "manualYaml": "kind: Service\napiVersion: v1\nmetadata: {name: s}\nspec: {ports: [{port: 80}]}\n"})
    secret = client.post(f"{BASE}/preview", json={"namespace": "app", "manualYaml": "kind: Secret\napiVersion: v1\nmetadata: {name: s}\n"})

    assert ok.status_code == 200 and ok.json()["objects"] == [{"kind": "Service", "name": "s"}]
    assert secret.status_code == 422 and secret.json()["detail"]["code"] == "SECRET_REFUSED"


def deploy(client, source, target, **changes):
    use(source=source, target=target)
    preview = client.post(f"{BASE}/preview", json={"namespace": "app"}).json()
    return client.post(f"{BASE}/deploy", json={"namespace": "app", "previewDigest": preview["previewDigest"], **changes})


def test_the_copy_is_created_on_the_disposable_cluster_only_with_the_digest_of_the_preview_the_person_saw(client):
    source, target = FakeSide("uid-source", {"deployments": [web()]}), FakeSide("uid-target")

    answer = deploy(client, source, target)

    assert answer.status_code == 200 and answer.json()["created"] == ["Deployment/web"]
    assert target.namespaces == ["resilience-validation-app"] and target.created == [(("Deployment", "web"), "resilience-validation-app")] and source.created == []
    assert deploy(client, source, target).json()["alreadyExisted"] == ["Deployment/web"]


def test_a_stale_digest_creates_nothing(client):
    source, target = FakeSide("uid-source", {"deployments": [web()]}), FakeSide("uid-target")
    use(source=source, target=target)

    answer = client.post(f"{BASE}/deploy", json={"namespace": "app", "previewDigest": "0" * 64})

    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "PREVIEW_CHANGED" and target.created == []


def test_a_copy_with_something_unresolved_is_not_created(client):
    item = web()
    item["spec"]["template"]["spec"] = {"containers": [{"name": "c"}], "imagePullSecrets": [{"name": "regcred"}]}
    source, target = FakeSide("uid-source", {"deployments": [item]}), FakeSide("uid-target")

    answer = deploy(client, source, target)

    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "COPY_NOT_DEPLOYABLE" and target.created == []


@pytest.mark.parametrize("target_uid", ["uid-source", None, ""])
def test_nothing_is_created_on_a_cluster_that_is_the_applications_own_or_cannot_be_identified(client, target_uid):
    source, target = FakeSide("uid-source", {"deployments": [web()]}), FakeSide(target_uid)

    answer = deploy(client, source, target)

    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "TARGET_IS_NOT_A_SEPARATE_CLUSTER" and target.created == [] and target.namespaces == []


def test_creating_the_copy_needs_the_deployment_to_have_armed_writes(client, monkeypatch):
    source, target = FakeSide("uid-source", {"deployments": [web()]}), FakeSide("uid-target")
    use(source=source, target=target)
    preview = client.post(f"{BASE}/preview", json={"namespace": "app"}).json()
    monkeypatch.delenv("RESILIENCE_ALLOW_CLUSTER_WRITES")

    answer = client.post(f"{BASE}/deploy", json={"namespace": "app", "previewDigest": preview["previewDigest"]})

    assert answer.status_code == 403 and target.created == []
