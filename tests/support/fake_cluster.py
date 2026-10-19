"""In-memory stand-in for a Kubernetes cluster, used by unit tests.

It implements the ClusterSource interface the discovery layer depends on, so no test needs a
kubeconfig, a network, or the developer's real cluster.
"""

from dataclasses import dataclass, field

from app.engine.deployment_topology_modeling.live_state.cluster_models import (
    ClusterDeployment,
    ClusterNode,
    ClusterPod,
)
from app.integrations.kubernetes.models import ClusterReplicaSet, ClusterService


def node(name: str, zone: str | None = "zone-a", *, cpu: str | None = "4", memory: str | None = "8Gi",
         control_plane: bool = False) -> ClusterNode:
    return ClusterNode(name=name, zone=zone, cpu_available=cpu, memory_available=memory,
                       control_plane=control_plane)


def pod(name: str, on: str | None, *, app: str, ready: bool = True, restarts: int | None = 0,
        phase: str | None = "Running", terminating: bool = False, owner: str | None = None,
        labels: dict[str, str] | None = None) -> ClusterPod:
    return ClusterPod(
        name=name, node=on, ready=ready,
        owner_kind="ReplicaSet", owner_name=owner or f"{app}-rs",
        app_label=app, labels=labels if labels is not None else ({"app": app} if app else {}),
        phase=phase, terminating=terminating, restart_count=restarts,
    )


def deployment(name: str, replicas: int) -> ClusterDeployment:
    return ClusterDeployment(name=name, declared_replicas=replicas)


def service(name: str, selector: dict[str, str]) -> ClusterService:
    return ClusterService(name=name, selector=dict(selector))


def replicaset(name: str, deployment: str | None) -> ClusterReplicaSet:
    return ClusterReplicaSet(name=name, deployment_name=deployment)


@dataclass
class FakeClusterSource:
    nodes: list[ClusterNode] = field(default_factory=list)
    pods: dict[str, list[ClusterPod]] = field(default_factory=dict)
    deployments: dict[str, list[ClusterDeployment]] = field(default_factory=dict)
    services: dict[str, list[ClusterService]] = field(default_factory=dict)
    replicasets: dict[str, list[ClusterReplicaSet]] = field(default_factory=dict)
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def _maybe_fail(self, what: str) -> None:
        self.calls.append(what)
        if self.error is not None:
            raise self.error

    def list_nodes(self) -> list[ClusterNode]:
        self._maybe_fail("nodes")
        return list(self.nodes)

    def list_pods(self, namespace: str) -> list[ClusterPod]:
        self._maybe_fail(f"pods:{namespace}")
        return list(self.pods.get(namespace, []))

    def list_deployments(self, namespace: str) -> list[ClusterDeployment]:
        self._maybe_fail(f"deployments:{namespace}")
        return list(self.deployments.get(namespace, []))

    def list_services(self, namespace: str) -> list[ClusterService]:
        self._maybe_fail(f"services:{namespace}")
        return list(self.services.get(namespace, []))

    def list_replicasets(self, namespace: str) -> list[ClusterReplicaSet]:
        self._maybe_fail(f"replicasets:{namespace}")
        return list(self.replicasets.get(namespace, []))

    def connection_fingerprint(self) -> str | None:
        return "fake-connection"


def bookinfo(reviews_versions: tuple[str, ...] = ("v2",)) -> FakeClusterSource:
    """Istio Bookinfo as it runs in the research cluster: Deployments named ``<app>-<version>``,
    Services named ``<app>`` selecting ``app=<app>``, pods owned through ReplicaSets.

    ``reviews_versions`` lets a test put several reviews Deployments behind the one ``reviews`` Service.
    """
    placement = {"productpage": "resilience-research-worker2", "details": "resilience-research-worker2",
                 "ratings": "resilience-research-worker", "reviews": "resilience-research-worker3"}
    versions = {"productpage": ("v1",), "details": ("v1",), "ratings": ("v1",), "reviews": reviews_versions}
    pods, deployments, replicasets = [], [], []
    for app, app_versions in versions.items():
        for version in app_versions:
            deploy = f"{app}-{version}"
            rs = f"{deploy}-5c6d4d854"
            deployments.append(deployment(deploy, 1))
            replicasets.append(replicaset(rs, deploy))
            pods.append(pod(f"{rs}-x{len(pods)}", placement[app], app=app, owner=rs,
                            labels={"app": app, "version": version}))
    return FakeClusterSource(
        nodes=[node("resilience-research-worker", None), node("resilience-research-worker2", None),
               node("resilience-research-worker3", None),
               node("resilience-research-control-plane", None, control_plane=True)],
        pods={"bookinfo": pods},
        deployments={"bookinfo": deployments},
        services={"bookinfo": [service(app, {"app": app}) for app in versions]},
        replicasets={"bookinfo": replicasets},
    )


def two_node_shop() -> FakeClusterSource:
    """A small, fully scheduled, spread-out application in namespace 'shop'."""
    return FakeClusterSource(
        nodes=[node("worker-1"), node("worker-2", "zone-b"), node("cp-1", None, control_plane=True)],
        pods={"shop": [
            pod("web-1", "worker-1", app="web"), pod("web-2", "worker-2", app="web"),
            pod("db-1", "worker-1", app="db"),
        ]},
        deployments={"shop": [deployment("web", 2), deployment("db", 1)]},
    )


def cluster_from_graph(graph, namespace: str = "default") -> FakeClusterSource:
    """A fake cluster whose live state is exactly ``graph`` (a DeploymentGraph used as a test fixture).

    Tests that formerly relied on the silent sample fallback use this to state their topology openly.
    """
    nodes = [node(n.name, n.zone, cpu=None if n.cpu_available is None else str(n.cpu_available),
                  memory=None if n.memory_available is None else f"{n.memory_available}Gi")
             for n in graph.nodes]
    pods, deployments = [], []
    for service in graph.services:
        deployments.append(deployment(service.name, service.declared_replicas))
        pods += [pod(p.name, p.node, app=service.name, ready=p.ready, restarts=p.restart_count)
                 for p in service.pods]
    return FakeClusterSource(nodes=nodes, pods={namespace: pods}, deployments={namespace: deployments})
