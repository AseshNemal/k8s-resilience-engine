"""An in-memory ``ClusterGateway`` for the test cluster, to exercise the Kubernetes executor without any cluster.

TEST-ONLY. Nothing here opens a connection, reads a kubeconfig or runs a command. It holds a ``DeploymentGraph`` and the
Deployments behind it, applies a patch the way a simple scheduler would, and records every contact in ``calls`` so a test
can prove what a refused step did not do.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import yaml

from app.engine.recommendation_evaluation_validation.validation.cluster_gateway import (
    TEST_DOUBLE,
    LiveDeployment,
)
from app.engine.recommendation_evaluation_validation.validation.cluster_identity import ClusterIdentity
from app.engine.recommendation_evaluation_validation.validation.node_fault import NodeFaultKind
from app.shared.schemas.deployment import DeploymentGraph, ServicePlacement

Scheduler = Callable[[DeploymentGraph, list[str]], None]


def live_deployments(graph: DeploymentGraph) -> dict[str, LiveDeployment]:
    """The Deployments behind a graph's services, as a cluster would report them (one per named Deployment)."""
    found = {}
    for service in graph.services:
        intent = service.declared_intent
        for name in service.deployment_names:
            found[name] = LiveDeployment(
                name=name, namespace=service.namespace, resource_version="rv-1", generation=1,
                replicas=service.declared_replicas,
                selector=dict(intent.selector) if intent else {}, pod_labels=dict(intent.selector) if intent else {},
                topology_spread_constraints=tuple(
                    {"topologyKey": c.topology_key, "maxSkew": c.max_skew, "whenUnsatisfiable": c.when_unsatisfiable,
                     "labelSelector": c.label_selector or {}} for c in (intent.topology_spread_constraints if intent else [])),
                pod_anti_affinity=dict(intent.pod_anti_affinity_rules) if intent and intent.pod_anti_affinity_rules else None)
    return found


def service_of(graph: DeploymentGraph, deployment: str) -> ServicePlacement | None:
    return next((s for s in graph.services if deployment in s.deployment_names), None)


class FakeClusterGateway:
    """A scripted cluster. Failures are injected by name so each safety path can be driven on purpose."""

    evidence_class = TEST_DOUBLE

    def __init__(self, graph: DeploymentGraph, *, uid: str = "6f1e2c1a-0000-4000-8000-0000000000aa",
                 api_server: str = "https://127.0.0.1:6443", context: str | None = "kind-resilience-test",
                 namespace: str = "default", scheduler: Scheduler | None = None, settles: bool = True,
                 unreadable_uid: bool = False, fail_at: str | None = None,
                 pending_after_patch: bool = False, detection_polls: int = 0, recovery_polls: int = 0) -> None:
        self.graph = graph.model_copy(deep=True)
        self.deployments = live_deployments(self.graph)
        self.uid, self.api_server, self.context, self.namespace = uid, api_server, context, namespace
        self.unreadable_uid, self.fail_at, self.pending_after_patch = unreadable_uid, fail_at, pending_after_patch
        self.settles = settles
        self.scheduler = scheduler
        self.not_ready: set[str] = set()
        # A real control plane notices a dead node only after a delay, and a started node takes a while to report Ready:
        # the number of times node_ready is asked before it changes its answer.
        self.detection_polls, self.recovery_polls = detection_polls, recovery_polls
        self._going_down: dict[str, int] = {}
        self._coming_up: dict[str, int] = {}
        self.calls: list[str] = []
        self._version = 1
        self._original: DeploymentGraph | None = None

    # -- reads ----------------------------------------------------------------------------------------------

    @property
    def wrote(self) -> bool:
        return any(call.startswith(("patch:", "restore:")) for call in self.calls)

    def identity(self) -> ClusterIdentity:
        self.calls.append("identity")
        return ClusterIdentity(self.context, self.api_server, None if self.unreadable_uid else self.uid,
                               tuple(n.name for n in self.graph.nodes))

    def read_graph(self, namespace: str) -> DeploymentGraph:
        self.calls.append("read_graph")
        failed = self.graph.model_copy(deep=True)
        for node in failed.nodes:
            node.ready = node.name not in self.not_ready
        for service in failed.services:
            for pod in service.pods:
                if pod.node in self.not_ready:
                    pod.ready = False
            service.ready_replicas = sum(1 for pod in service.pods if pod.ready)
        return failed

    def read_deployments(self, namespace: str) -> Mapping[str, LiveDeployment]:
        self.calls.append("read_deployments")
        return dict(self.deployments)

    def rollout_complete(self, namespace: str, names: Sequence[str]) -> bool:
        self.calls.append("rollout_complete")
        return self.settles

    def node_ready(self, node: str) -> bool | None:
        self.calls.append(f"node_ready:{node}")
        if node in self._going_down:
            if self._going_down[node] > 0:
                self._going_down[node] -= 1
                return True         # the control plane has not noticed yet
            del self._going_down[node]
            self.not_ready.add(node)
        if node in self._coming_up:
            if self._coming_up[node] > 0:
                self._coming_up[node] -= 1
                return False        # started, not yet reporting Ready
            del self._coming_up[node]
        return node not in self.not_ready

    # -- writes ---------------------------------------------------------------------------------------------

    def patch_deployment(self, document: dict[str, Any], *, expect_resource_version: str) -> None:
        name = document["metadata"]["name"]
        self.calls.append(f"patch:{name}")
        if self.fail_at == "patch":
            raise RuntimeError("the API server refused the patch")
        current = self.deployments[name]
        if current.resource_version != expect_resource_version:
            raise RuntimeError("the Deployment changed since it was read")
        spec = document.get("spec") or {}
        pod_spec = ((spec.get("template") or {}).get("spec")) or {}
        self._version += 1
        replicas = spec.get("replicas", current.replicas)
        spread = tuple(pod_spec["topologySpreadConstraints"]) if "topologySpreadConstraints" in pod_spec else current.topology_spread_constraints
        anti = {**(current.pod_anti_affinity or {}), **(pod_spec.get("affinity") or {}).get("podAntiAffinity", {})} \
            if "affinity" in pod_spec else current.pod_anti_affinity
        self.deployments[name] = LiveDeployment(
            name=name, namespace=current.namespace, resource_version=f"rv-{self._version}", generation=current.generation + 1,
            replicas=replicas, selector=current.selector, pod_labels=current.pod_labels,
            topology_spread_constraints=spread, pod_anti_affinity=anti)
        service = service_of(self.graph, name)
        if service is not None:
            while len(service.pods) < replicas:      # the controller creates the missing pods
                service.pods.append(service.pods[0].model_copy(update={"name": f"{service.name}-{len(service.pods)}"}))
            service.declared_replicas = len(service.pods)
            if self.scheduler is not None:
                self.scheduler(self.graph, [service.name])
            if self.pending_after_patch:      # the scheduler could not place the last pod: it is observed, not assumed
                service.pods[-1].ready, service.pods[-1].phase = False, "Pending"
                service.ready_replicas = sum(1 for pod in service.pods if pod.ready)

    def restore_deployment(self, snapshot: LiveDeployment) -> None:
        self.calls.append(f"restore:{snapshot.name}")
        if self.fail_at == "restore":
            raise RuntimeError("the API server refused the restore")
        self._version += 1
        self.deployments[snapshot.name] = LiveDeployment(
            **{**snapshot.__dict__, "resource_version": f"rv-{self._version}",
               "generation": self.deployments[snapshot.name].generation + 1})
        service = service_of(self.graph, snapshot.name)
        if service is not None and self._original is not None:
            original = service_of(self._original, snapshot.name)
            service.pods = [pod.model_copy(deep=True) for pod in original.pods]
            service.declared_replicas = original.declared_replicas

    def remember_original(self) -> None:
        """Test hook: where pods go back to when a Deployment is restored (a controller would recreate them)."""
        self._original = self.graph.model_copy(deep=True)

    def fail_node(self, node: str) -> None:
        """Test hook for the node-fault injector double: the node stops answering."""
        if self.detection_polls:
            self._going_down[node] = self.detection_polls
        else:
            self.not_ready.add(node)

    def recover_node(self, node: str) -> None:
        self._going_down.pop(node, None)
        self.not_ready.discard(node)
        if self.recovery_polls:
            self._coming_up[node] = self.recovery_polls


def spread_least_loaded(graph: DeploymentGraph, services: list[str]) -> None:
    """Least-loaded node first, ties by name (what a working DoNotSchedule/maxSkew 1 constraint tends to produce)."""
    names = sorted(n.name for n in graph.nodes if (n.labels or {}).get("node-role.kubernetes.io/control-plane") is None)
    for service in graph.services:
        if service.name not in services:
            continue
        load = Counter({name: 0 for name in names})
        for pod in service.pods:
            node = min(names, key=lambda n: (load[n], n))
            load[node] += 1
            pod.node = node


def parse_documents(yaml_text: str) -> list[dict[str, Any]]:
    return [doc for doc in yaml.safe_load_all(yaml_text) if doc]


class FakeClock:
    """Time that only moves when the code under test sleeps, so a timeout path runs instantly and deterministically."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeNodeFault:
    """A node-fault injector double. ``works=False`` models a stop that did not take; ``restores=False`` a node that stays down."""

    evidence_class = TEST_DOUBLE
    method = "in-memory double (no node is stopped)"

    def __init__(self, gateway: FakeClusterGateway, kind: NodeFaultKind = NodeFaultKind.NODE_STOP, *,
                 works: bool = True, restores: bool = True) -> None:
        self.gateway, self.kind, self.works, self.restores = gateway, kind, works, restores
        self.calls: list[str] = []

    def inject(self, node: str) -> None:
        self.calls.append(f"inject:{node}")
        if self.works:
            self.gateway.fail_node(node)

    def restore(self, node: str) -> None:
        self.calls.append(f"restore:{node}")
        if self.restores:
            self.gateway.recover_node(node)


class FakeWorkflowBackend:
    """The HTTP answer of an application whose pods run where the gateway says they do (TEST-ONLY).

    A request succeeds while every mandatory service has a pod on a node that is up and the service is not ``broken``. A
    ``broken`` service has a ready pod but cannot do its job, which is the case where readiness and a request disagree.
    """

    OK = "Book Details ... Book Reviews ..."
    DEGRADED = "Book Details Sorry, product reviews are currently unavailable for this book."

    def __init__(self, gateway: FakeClusterGateway, mandatory: Sequence[str], *, broken: Sequence[str] = (),
                 raises: bool = False) -> None:
        self.gateway, self.mandatory, self.broken, self.raises, self.requests = gateway, list(mandatory), set(broken), raises, 0

    def __call__(self, spec):
        from app.engine.recommendation_evaluation_validation.validation.workflow_probe import FetchResult
        self.requests += 1
        if self.raises:
            raise RuntimeError("the fetch itself blew up")
        alive = all(
            name not in self.broken and any(pod.node not in self.gateway.not_ready for pod in service.pods)
            for name in self.mandatory for service in self.gateway.graph.services if service.name == name)
        return FetchResult(200, self.OK if alive else self.DEGRADED, 5.0, None)
