from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterPod
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import resolve_service_ownership
from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import pods_from_objects
from tests.deployment_topology_modeling import kind_snapshot


def _deployment(name, uid, replicas, labels):
    return {"metadata": {"name": name, "uid": uid},
            "spec": {"replicas": replicas, "template": {"metadata": {"labels": labels}}}}


def _rs(name, uid, deployment_uid):
    return {"metadata": {"name": name, "uid": uid,
                         "ownerReferences": [{"kind": "Deployment", "uid": deployment_uid, "controller": True}]}}


def _service(name, selector):
    return {"metadata": {"name": name}, "spec": {"selector": selector}}


def _pod(name, labels, rs_name=None, rs_uid=None, ready=True, phase="Running", owner_kind="ReplicaSet"):
    return ClusterPod(name=name, node="n1", ready=ready, owner_kind=owner_kind if rs_name else None,
                      owner_name=rs_name, app_label=labels.get("app", ""), labels=labels,
                      namespace="default", owner_uid=rs_uid, phase=phase)


def test_real_snapshot_service_pod_deployment_chain():
    pods = pods_from_objects(kind_snapshot.pod_objects(), kind_snapshot.NAMESPACE)
    result = resolve_service_ownership(
        pods, kind_snapshot.service_objects(), kind_snapshot.replica_set_objects(),
        kind_snapshot.deployment_objects(), kind_snapshot.NAMESPACE,
    )
    pay = result["paymentservice"]
    assert pay["deploymentNames"] == ["paymentservice"]
    assert pay["declaredReplicas"] == 3
    assert pay["readyReplicas"] == 3
    assert sorted(p["name"] for p in pay["pods"]) == [
        "paymentservice-74d644658f-rmmc5", "paymentservice-74d644658f-rw5hq", "paymentservice-74d644658f-tgzzd",
    ]
    assert pay["podsWithoutDeploymentOwner"] == []


def test_membership_comes_from_selector_not_names():
    deployments = [_deployment("web", "d-web", 3, {"app": "web"})]
    replica_sets = [_rs("web-rs", "rs-web", "d-web")]
    pods = [
        _pod("web-1", {"app": "web"}, "web-rs", "rs-web"),
        _pod("web-2", {"app": "web"}, "web-rs", "rs-web", ready=False),
        _pod("web-imposter", {"app": "other"}, "web-rs", "rs-web"),       # name looks like web, labels do not
        _pod("old", {"app": "web"}, "web-rs", "rs-web", phase="Succeeded"),  # terminal pods are not replicas
    ]
    result = resolve_service_ownership(pods, [_service("web-svc", {"app": "web"})], replica_sets, deployments)
    web = result["web-svc"]
    assert [p["name"] for p in web["pods"]] == ["web-1", "web-2"]
    assert web["readyReplicas"] == 1
    assert web["declaredReplicas"] == 3


def test_owner_resolved_by_uid_not_name():
    deployments = [_deployment("web", "d-web", 2, {"app": "web"})]
    replica_sets = [_rs("web-rs", "rs-current", "d-web")]
    # Pod points at a ReplicaSet with the same name but a different (stale) UID.
    pods = [_pod("web-1", {"app": "web"}, "web-rs", "rs-stale")]
    web = resolve_service_ownership(pods, [_service("web", {"app": "web"})], replica_sets, deployments)["web"]
    assert web["pods"][0]["deploymentName"] is None
    assert web["podsWithoutDeploymentOwner"] == ["web-1"]


def test_non_deployment_pods_still_belong_to_the_service():
    pods = [_pod("db-0", {"app": "db"}, rs_name="db", rs_uid="sts", owner_kind="StatefulSet")]
    db = resolve_service_ownership(pods, [_service("db", {"app": "db"})], [], [])["db"]
    assert [p["name"] for p in db["pods"]] == ["db-0"]
    assert db["deploymentNames"] == []
    assert db["podsWithoutDeploymentOwner"] == ["db-0"]


def test_deployment_with_zero_pods_still_declares_replicas():
    deployments = [_deployment("cart", "d-cart", 2, {"app": "cart"})]
    cart = resolve_service_ownership([], [_service("cart", {"app": "cart"})], [], deployments)["cart"]
    assert cart["pods"] == []
    assert cart["declaredReplicas"] == 2
    assert cart["deploymentNames"] == ["cart"]


def test_service_without_selector_is_skipped():
    assert resolve_service_ownership([], [_service("external", {})], [], []) == {}
