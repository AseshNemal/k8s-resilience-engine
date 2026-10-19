from app.engine.deployment_topology_modeling.live_state.cluster_models import ClusterDeployment, ClusterPod
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import (
    find_unattributed_pods,
    related_deployments,
    resolve_deployment_ownership,
)


def make_pod(name, node, app, ready=True, **extra):
    return ClusterPod(name=name, node=node, ready=ready, owner_kind="ReplicaSet", owner_name=f"{app}-rs",
                      app_label=app, labels={"app": app}, **extra)


DEPLOYMENTS = [ClusterDeployment(name="web-service", declared_replicas=3)]


def test_resolve_deployment_ownership_mapping():
    pods = [
        make_pod("web-1", "node-a", "web-service", ready=True),
        make_pod("web-2", "node-b", "web-service", ready=False),
        make_pod("db-1", "node-c", "db-service", ready=True),
    ]

    workloads = resolve_deployment_ownership(pods, DEPLOYMENTS)

    assert workloads["web-service"]["declaredReplicas"] == 3
    assert workloads["web-service"]["readyReplicas"] == 1  # only web-1 is ready
    assert [p["name"] for p in workloads["web-service"]["pods"]] == ["web-1", "web-2"]
    assert "db-service" not in workloads  # no such deployment: not invented


def test_unattributed_pods_are_found_not_guessed():
    pods = [make_pod("web-1", "node-a", "web-service"), make_pod("db-1", "node-c", "db-service"),
            make_pod("anon-1", "node-c", "")]

    assert [p.name for p in find_unattributed_pods(pods, DEPLOYMENTS)] == ["db-1", "anon-1"]


def test_pod_without_a_node_is_kept_apart_not_placed():
    pods = [make_pod("web-1", None, "web-service", ready=False, phase="Pending")]

    workload = resolve_deployment_ownership(pods, DEPLOYMENTS)["web-service"]

    assert workload["pods"] == []
    assert workload["unscheduledPods"] == ["web-1"]


def test_terminating_pod_is_never_ready_even_if_the_source_says_so():
    pods = [make_pod("web-1", "node-a", "web-service", ready=True, terminating=True)]

    workload = resolve_deployment_ownership(pods, DEPLOYMENTS)["web-service"]

    assert workload["readyReplicas"] == 0
    assert workload["pods"][0]["ready"] is False


def test_relation_by_name_is_conservative_and_only_a_hint():
    related = ClusterPod(name="x-1", node="n", ready=True, owner_kind="ReplicaSet", owner_name="web-service-5d9",
                         app_label="other", labels={})

    assert related_deployments(related, DEPLOYMENTS) == ["web-service"]
    assert related_deployments(make_pod("db-1", "n", "db-service"), DEPLOYMENTS) == []
