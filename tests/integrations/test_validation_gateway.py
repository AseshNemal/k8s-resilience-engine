"""The gateway to a designated test cluster: what it reads, that it is read-only by default, and that it borrows no credentials.

The API server is a fake over ``httpx.MockTransport``; Member 2's real ``discover`` runs over its answers. Nothing here
opens a network connection, reads the developer's kubeconfig, or writes to a cluster.
"""

import ast
from pathlib import Path

import pytest

from app.engine.deployment_topology_modeling.discovery import discover
from app.engine.recommendation_evaluation_validation.validation import (
    DisposableClusterDesignation,
    ValidationRefusedError,
    verify_cluster,
)
from app.engine.recommendation_evaluation_validation.validation.kubernetes_executor import KubernetesValidationExecutor
from app.engine.recommendation_evaluation_validation.validation.node_fault import NodeFaultKind
from app.integrations.kubernetes.quantity import parse_quantity
from app.integrations.kubernetes.validation_gateway import (
    AmbientCredentialsError,
    ConflictError,
    KubernetesTestClusterGateway,
    WritesDisabledError,
)
from tests.support.member4_fake_apiserver import KUBE_SYSTEM_UID, NAMESPACE, FakeApiServer, node_json, write_kubeconfig

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def api():
    return FakeApiServer()


def gateway(api, tmp_path, **options):
    path = write_kubeconfig(tmp_path / "validation.kubeconfig")
    return KubernetesTestClusterGateway(path, topology_reader=discover, transport=api.transport, **options)


def designation(**overrides):
    values = dict(name="kind-resilience-validation", api_server="https://127.0.0.1:41234", cluster_uid=KUBE_SYSTEM_UID,
                  namespace=NAMESPACE, designated_by="asesh")
    return DisposableClusterDesignation(**{**values, **overrides})


# --- whose credentials ------------------------------------------------------------------------------------------------

def test_the_default_kubeconfig_is_never_borrowed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".kube").mkdir(parents=True)
    ambient = Path(write_kubeconfig(home / ".kube" / "config"))
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(AmbientCredentialsError):
        KubernetesTestClusterGateway(str(ambient), topology_reader=discover)


def test_a_file_named_in_the_kubeconfig_variable_is_not_borrowed_either(tmp_path, monkeypatch):
    shared = write_kubeconfig(tmp_path / "shared")
    monkeypatch.setenv("KUBECONFIG", f"{tmp_path / 'other'}:{shared}")

    with pytest.raises(AmbientCredentialsError):
        KubernetesTestClusterGateway(shared, topology_reader=discover)


def test_the_connected_cluster_is_the_one_explicit_exception_and_it_is_a_separate_switch(api, tmp_path, monkeypatch):
    shared = write_kubeconfig(tmp_path / "shared")
    monkeypatch.setenv("KUBECONFIG", shared)

    with pytest.raises(AmbientCredentialsError):             # the same file, without the switch, is still refused
        KubernetesTestClusterGateway(shared, topology_reader=discover)
    connected = KubernetesTestClusterGateway(shared, connected_cluster=True, allow_writes=True, transport=api.transport)

    assert connected.allow_writes is True and connected.identity().cluster_uid == KUBE_SYSTEM_UID


def test_the_connected_cluster_still_needs_a_file_that_exists(tmp_path):
    for bad in ("", str(tmp_path / "absent")):
        with pytest.raises(AmbientCredentialsError):
            KubernetesTestClusterGateway(bad, connected_cluster=True)


def test_a_gateway_made_for_applying_has_no_topology_reader_and_says_so(api, tmp_path):
    connected = KubernetesTestClusterGateway(write_kubeconfig(tmp_path / "c"), connected_cluster=True, transport=api.transport)

    from app.integrations.kubernetes.client import NotConnectedError
    with pytest.raises(NotConnectedError):
        connected.read_graph(NAMESPACE)


def test_no_path_and_a_missing_file_are_refused_rather_than_defaulted(tmp_path):
    for bad in ("", "   ", str(tmp_path / "absent")):
        with pytest.raises(AmbientCredentialsError):
            KubernetesTestClusterGateway(bad, topology_reader=discover)


# --- reads --------------------------------------------------------------------------------------------------------------

def test_identity_is_the_kube_system_uid_the_address_the_context_and_every_node(api, tmp_path):
    identity = gateway(api, tmp_path).identity()

    assert identity.cluster_uid == KUBE_SYSTEM_UID and identity.api_server == "https://127.0.0.1:41234"
    assert identity.context == "kind-resilience-validation" and set(identity.node_names) == {"cp", "w1", "w2", "w3"}


def test_a_uid_that_cannot_be_read_is_none_not_invented(tmp_path):
    api = FakeApiServer(uid=None)

    assert gateway(api, tmp_path).identity().cluster_uid is None


def test_the_graph_is_member_2s_own_and_holds_workers_only_with_node_state_read_from_the_nodes(tmp_path):
    api = FakeApiServer(nodes={"cp": node_json("cp", control_plane=True), "w1": node_json("w1"),
                               "w2": node_json("w2", ready="Unknown"), "w3": node_json("w3", unschedulable=True)})

    graph = gateway(api, tmp_path).read_graph(NAMESPACE)

    assert {n.name for n in graph.nodes} == {"w1", "w2", "w3"}                  # the control plane is never a failure domain
    assert {n.name: n.ready for n in graph.nodes} == {"w1": True, "w2": False, "w3": True}
    assert {n.name: n.schedulable for n in graph.nodes} == {"w1": True, "w2": True, "w3": False}
    reviews = next(s for s in graph.services if s.name == "reviews")
    assert [p.node for p in reviews.pods] == ["w1", "w1", "w1"] and reviews.deployment_names == ["reviews"]


def test_a_pod_the_cluster_reports_not_ready_is_not_ready_in_the_graph(tmp_path):
    api = FakeApiServer()
    api.pod_ready["reviews-1"] = False

    reviews = next(s for s in gateway(api, tmp_path).read_graph(NAMESPACE).services if s.name == "reviews")

    assert [p.ready for p in reviews.pods] == [True, False, True] and reviews.ready_replicas == 2


def test_deployments_carry_the_version_the_selector_and_the_constraints_a_patch_is_checked_against(api, tmp_path):
    deployments = gateway(api, tmp_path).read_deployments(NAMESPACE)

    reviews = deployments["reviews"]
    assert reviews.resource_version == "rv-reviews-1" and reviews.generation == 3 and reviews.replicas == 3
    assert reviews.selector == {"app": "reviews"} and reviews.pod_labels == {"app": "reviews"}
    assert reviews.has_hostname_spread is False and reviews.has_required_anti_affinity is False


def test_a_rollout_is_complete_only_when_every_replica_is_updated_and_available(tmp_path):
    api = FakeApiServer()
    g = gateway(api, tmp_path)
    assert g.rollout_complete(NAMESPACE, ["reviews", "details"]) is True

    api.rollout_complete = False
    assert g.rollout_complete(NAMESPACE, ["reviews"]) is False
    assert g.rollout_complete(NAMESPACE, ["no-such-deployment"]) is False


@pytest.mark.parametrize("status, expected", [("True", True), ("False", False), ("Unknown", False)])
def test_node_readiness_treats_unknown_as_not_ready(tmp_path, status, expected):
    api = FakeApiServer(nodes={"w1": node_json("w1", ready=status)})

    assert gateway(api, tmp_path).node_ready("w1") is expected


def test_capacity_is_allocatable_minus_the_requests_of_every_pod_on_the_node(tmp_path):
    api = FakeApiServer(layout={"reviews": ["w1", "w1", "w2"]})
    api.pod_requests = {"cpu": "500m", "memory": "256Mi"}

    report = {c.name: c for c in gateway(api, tmp_path).read_capacity()}

    assert report["w1"].requested_cpu == pytest.approx(1.0) and report["w1"].free_cpu == pytest.approx(3.0)
    assert report["w1"].requested_memory == pytest.approx(512 * 1024**2) and report["w1"].pods == 2
    assert report["w2"].pods == 1 and report["w3"].pods == 0 and report["w3"].free_cpu == pytest.approx(4.0)
    assert report["cp"].control_plane is True and report["w1"].control_plane is False


@pytest.mark.parametrize("text, value", [("250m", 0.25), ("2", 2.0), ("512Mi", 512 * 1024**2), ("1Gi", 1024**3),
                                         ("1G", 1e9), ("1e3", 1000.0), (4, 4.0)])
def test_quantities_are_read_to_plain_numbers(text, value):
    assert parse_quantity(text) == pytest.approx(value)


@pytest.mark.parametrize("text", ["", "abc", "12Zi", None, True])
def test_an_unreadable_quantity_is_none_never_zero(text):
    assert parse_quantity(text) is None


# --- read-only by default ---------------------------------------------------------------------------------------------------

def test_a_default_gateway_sends_nothing_but_gets_and_refuses_to_write(api, tmp_path):
    g = gateway(api, tmp_path)
    g.identity(); g.read_graph(NAMESPACE); g.read_deployments(NAMESPACE); g.rollout_complete(NAMESPACE, ["reviews"])
    g.node_ready("w1"); g.read_capacity()
    document = {"metadata": {"name": "reviews", "namespace": NAMESPACE}, "spec": {"replicas": 4}}

    with pytest.raises(WritesDisabledError):
        g.patch_deployment(document, expect_resource_version="rv-reviews-1")
    with pytest.raises(WritesDisabledError):
        g.restore_deployment(g.read_deployments(NAMESPACE)["reviews"])

    assert api.methods == {"GET"} and api.patches() == []


# --- writes: exercised against the fake server only ----------------------------------------------------------------------------

def test_an_enabled_patch_is_a_strategic_merge_carrying_the_version_it_expects(api, tmp_path):
    g = gateway(api, tmp_path, allow_writes=True)
    document = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "reviews", "namespace": NAMESPACE},
                "spec": {"template": {"spec": {"topologySpreadConstraints": [{"topologyKey": "kubernetes.io/hostname"}]}}}}

    g.patch_deployment(document, expect_resource_version="rv-reviews-1")
    ((path, body, content_type),) = api.patches()

    assert path == f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/reviews"
    assert content_type == "application/strategic-merge-patch+json"
    assert body["metadata"]["resourceVersion"] == "rv-reviews-1" and "resourceVersion" not in document["metadata"]


def test_a_patch_refused_because_the_object_moved_is_a_conflict(api, tmp_path):
    api.patch_status = 409
    g = gateway(api, tmp_path, allow_writes=True)

    with pytest.raises(ConflictError):
        g.patch_deployment({"metadata": {"name": "reviews", "namespace": NAMESPACE}}, expect_resource_version="stale")


def test_a_restore_sets_back_what_was_there_and_removes_what_was_added(api, tmp_path):
    g = gateway(api, tmp_path, allow_writes=True)
    before = g.read_deployments(NAMESPACE)["reviews"]

    g.restore_deployment(before)
    ((_, body, _),) = api.patches()

    pod_spec = body["spec"]["template"]["spec"]
    assert body["spec"]["replicas"] == 3 and pod_spec["topologySpreadConstraints"] is None
    assert pod_spec["affinity"] == {"podAntiAffinity": None} and "resourceVersion" not in body.get("metadata", {})


def test_a_restore_replaces_the_spread_list_so_an_entry_added_under_another_key_does_not_survive(api, tmp_path):
    """A strategic merge patch merges this list by topologyKey: sending the old zone entry alone would leave an added hostname entry."""
    from app.engine.recommendation_evaluation_validation.validation.cluster_gateway import LiveDeployment
    zone = {"maxSkew": 1, "topologyKey": "topology.kubernetes.io/zone", "whenUnsatisfiable": "ScheduleAnyway"}
    g = gateway(api, tmp_path, allow_writes=True)
    before = g.read_deployments(NAMESPACE)["reviews"]
    snapshot = LiveDeployment(name=before.name, namespace=before.namespace, resource_version="1", generation=1, replicas=2,
                              selector=before.selector, pod_labels=before.pod_labels, topology_spread_constraints=(zone,))

    g.restore_deployment(snapshot)
    ((_, body, _),) = api.patches()

    assert body["spec"]["template"]["spec"]["topologySpreadConstraints"] == [{"$patch": "replace"}, zone]


# --- it is what the engine's gateway protocol asks for -------------------------------------------------------------------------

def test_the_engines_identity_check_accepts_the_real_gateways_answer_for_the_designated_cluster(api, tmp_path):
    target = verify_cluster(gateway(api, tmp_path).identity(), designation())

    assert target.name == "kind-resilience-validation" and target.environment == "test"


def test_the_engines_identity_check_refuses_it_for_a_cluster_that_looks_like_docker_desktop(tmp_path):
    api = FakeApiServer(nodes={"desktop-control-plane": node_json("desktop-control-plane", control_plane=True),
                               "desktop-worker": node_json("desktop-worker"), "desktop-worker2": node_json("desktop-worker2")},
                        layout={"reviews": ["desktop-worker"]})

    with pytest.raises(ValidationRefusedError) as raised:
        verify_cluster(gateway(api, tmp_path).identity(), designation())

    assert raised.value.code == "RESEARCH_CLUSTER_REFUSED"


class _Stopper:
    kind, evidence_class, method = NodeFaultKind.NODE_STOP, "TEST_DOUBLE", "unused"

    def inject(self, node): raise AssertionError("no failure is injected in this test")
    def restore(self, node): raise AssertionError("nothing to restore")


def test_the_executor_builds_over_the_real_gateway_and_captures_the_baseline_with_reads_only(api, tmp_path):
    g = gateway(api, tmp_path)

    executor = KubernetesValidationExecutor(g, _Stopper(), designation())

    assert executor.target.name == "kind-resilience-validation" and executor.evidence_class == "TEST_DOUBLE"   # the injector is a stub
    assert {s.name for s in executor.baseline_graph.services} == {"productpage", "details", "reviews", "ratings"}
    assert api.methods == {"GET"}


def test_the_executor_refuses_a_cluster_whose_uid_differs_from_the_designation(api, tmp_path):
    with pytest.raises(ValidationRefusedError) as raised:
        KubernetesValidationExecutor(gateway(api, tmp_path), _Stopper(), designation(cluster_uid="0" * 8 + "-other"))

    assert raised.value.code == "CLUSTER_IDENTITY_MISMATCH" and api.methods == {"GET"}


def test_a_gateway_is_real_only_when_it_talks_over_the_network(api, tmp_path):
    path = write_kubeconfig(tmp_path / "own.kubeconfig")

    assert gateway(api, tmp_path).evidence_class == "TEST_DOUBLE"
    assert KubernetesTestClusterGateway(path, topology_reader=discover).evidence_class == "REAL_CLUSTER"      # no transport: not contacted here


# --- layering ----------------------------------------------------------------------------------------------------------------------

def test_the_integration_modules_import_nothing_from_the_engine():
    for name in ("validation_gateway.py", "quantity.py"):
        tree = ast.parse((ROOT / "app" / "integrations" / "kubernetes" / name).read_text())
        modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [m for m in modules if m.startswith("app.engine")], (name, modules)


def test_the_shared_rest_client_gained_no_way_to_write():
    source = (ROOT / "app" / "integrations" / "kubernetes" / "rest.py").read_text()

    assert "PATCH" not in source and "def patch" not in source and "def delete" not in source
