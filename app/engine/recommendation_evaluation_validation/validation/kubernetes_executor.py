"""A ``ValidationExecutor`` for a real Kubernetes test cluster: all the safety decisions, none of the cluster contact.

``ValidationRun`` decides WHAT may happen and WHEN (an approved plan, a named person's authorization per step, the
order of steps). This executor decides whether the cluster in front of it is allowed to be touched at all, and whether
what it was asked to do is safe to do to it. The contact itself goes through a ``ClusterGateway`` and a
``NodeFaultInjector``; a gateway for a real cluster does not exist yet, so nothing here can reach one today.

Decisions it makes, each before anything changes:

* Identity. The cluster must be the one a person designated (kube-system UID), and not the normal research cluster. The
  identity is read when the executor is built and again before every write, so connection settings that are switched to another
  cluster half-way through is caught before the next change.
* The fault. Only an injector that stops a node (``NODE_STOP``) is accepted. A cordon and drain is refused.
* A clean baseline. The experiment namespace is captured as it is, and must be a healthy steady state with enough worker
  nodes; a cluster that is already degraded cannot show what a change did.
* The text. The proposal is checked against the Deployments in the cluster (``proposal_check``) and against the versions
  captured with the baseline, so a Deployment edited in between is not patched.
* Writes are off by default. ``writes_enabled`` must be set deliberately; ``ValidationRun``'s authorizations are about the
  person's consent, this is about the process being armed.

What it observes instead of assuming. "Settled" means the rollout reports complete AND every pod of the affected services
is Ready and none is Pending, read back from the cluster; a change the scheduler could not place is not a settled change.
A node failure is only an experiment once the node is observed NotReady and the pods on it are observed not Ready; if the
stop did not take, nothing was measured and the run ends FAILED instead of reporting a failure that did not happen.

Restoring. ``restore_node`` starts the node and waits until it reports Ready. ``revert`` puts back the replica count and the
placement constraints of every Deployment this executor patched, from the versions read just before the patch, and waits
for the rollout. If either fails it raises, and the run reports ``needsManualRestore``; restore points are kept so it can
be tried again.

Reading is not free of consequence either: ``observe`` re-reads the live placement every time and caches nothing.
"""

import time
from collections.abc import Callable
from typing import Any

from app.engine.recommendation_evaluation_validation.validation.cluster_gateway import (
    REAL_CLUSTER,
    TEST_DOUBLE,
    ClusterGateway,
    LiveDeployment,
)
from app.engine.recommendation_evaluation_validation.validation.cluster_identity import (
    DisposableClusterDesignation,
    ResearchClusterAuthorization,
    verify_cluster,
)
from app.engine.recommendation_evaluation_validation.validation.models import TestTarget, ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation.node_fault import (
    NodeFaultInjector,
    require_node_failure,
)
from app.engine.recommendation_evaluation_validation.validation.proposal_check import (
    DEFAULT_MAX_ADDED_REPLICAS,
    check_proposal,
)
from app.shared.schemas.deployment import DeploymentGraph, NodeModel

CONTROL_PLANE_LABELS = ("node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master")


def _is_worker(node: NodeModel) -> bool:
    return not any(label in (node.labels or {}) for label in CONTROL_PLANE_LABELS)


class KubernetesValidationExecutor:
    """Implements ``ValidationExecutor`` over a verified, designated test cluster. Not usable until its checks pass."""

    def __init__(
        self, gateway: ClusterGateway, injector: NodeFaultInjector, designation: DisposableClusterDesignation, *,
        research_override: ResearchClusterAuthorization | None = None, writes_enabled: bool = False,
        min_worker_nodes: int = 2, max_added_replicas: int = DEFAULT_MAX_ADDED_REPLICAS,
        settle_timeout_s: float = 300.0, failure_detect_timeout_s: float = 120.0, pods_unready_timeout_s: float = 60.0,
        restore_timeout_s: float = 300.0, poll_interval_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._injector = require_node_failure(injector)       # refused before the cluster is even contacted
        self._gateway, self._designation, self._override = gateway, designation, research_override
        self.writes_enabled, self._max_added = writes_enabled, max_added_replicas
        self._settle_s, self._detect_s, self._unready_s = settle_timeout_s, failure_detect_timeout_s, pods_unready_timeout_s
        self._restore_s, self._poll_s, self._sleep, self._clock = restore_timeout_s, poll_interval_s, sleep, clock
        self.namespace = designation.namespace
        self.target: TestTarget = self._verified_target()

        graph = gateway.read_graph(self.namespace)
        workers = [node for node in graph.nodes if _is_worker(node)]
        if len(workers) < min_worker_nodes:
            raise ValidationRefusedError("TOPOLOGY_TOO_SMALL", f"The test cluster has {len(workers)} worker node(s); a node "
                                         f"failure experiment needs at least {min_worker_nodes} so something can survive.")
        unhealthy = [n.name for n in graph.nodes if n.ready is False] + [
            f"{pod.name} (on {pod.node})" for svc in graph.services for pod in svc.pods if not pod.ready]
        if unhealthy:
            raise ValidationRefusedError("BASELINE_UNHEALTHY", "The experiment namespace is not in a clean steady state, so "
                                         "it cannot be a baseline: " + ", ".join(unhealthy[:5]) + ".")
        self.baseline_graph: DeploymentGraph = graph
        self._baseline = dict(gateway.read_deployments(self.namespace))
        self._baseline_versions = {name: d.resource_version for name, d in self._baseline.items()}
        self._restore_points: dict[str, LiveDeployment] = {}
        self._affected: list[str] = []

    # -- identity and arming ---------------------------------------------------------------------------------

    @property
    def evidence_class(self) -> str:
        """REAL_CLUSTER only if both the gateway and the injector act on a real cluster; anything less is a double."""
        both = (getattr(self._gateway, "evidence_class", TEST_DOUBLE), getattr(self._injector, "evidence_class", TEST_DOUBLE))
        return REAL_CLUSTER if all(kind == REAL_CLUSTER for kind in both) else TEST_DOUBLE

    def _verified_target(self) -> TestTarget:
        return verify_cluster(self._gateway.identity(), self._designation, research_override=self._override)

    def _assert_same_cluster(self) -> None:
        if self._verified_target() != self.target:
            raise ValidationRefusedError("CLUSTER_IDENTITY_MISMATCH", "The connected cluster is no longer the one verified.")

    def _require_writes(self) -> None:
        if not self.writes_enabled:
            raise ValidationRefusedError("WRITES_DISABLED", "This executor was not armed for writes; set writes_enabled "
                                         "deliberately to let it change the test cluster.")

    def describe(self) -> dict[str, Any]:
        """What the experiment record keeps about the environment the run happened in."""
        identity = self._gateway.identity()
        return {
            "evidenceClass": self.evidence_class, "target": self.target.name, "namespace": self.namespace,
            "designatedBy": self._designation.designated_by,
            "cluster": {"apiServer": identity.api_server, "kubeSystemUid": identity.cluster_uid,
                        "context": identity.context, "nodes": list(identity.node_names)},
            "nodeFault": {"kind": self._injector.kind.value, "method": self._injector.method},
            "researchClusterAuthorizedBy": self._override.confirmed_by if self._override else None,
            "baselineDeploymentVersions": dict(self._baseline_versions),
        }

    # -- ValidationExecutor ---------------------------------------------------------------------------------------

    def observe(self) -> DeploymentGraph:
        self._assert_same_cluster()
        return self._gateway.read_graph(self.namespace)

    def apply(self, yaml_text: str) -> None:
        self._require_writes()
        self._assert_same_cluster()
        live = self._gateway.read_deployments(self.namespace)
        patches = check_proposal(yaml_text, live, namespace=self.namespace, baseline_versions=self._baseline_versions,
                                 max_added_replicas=self._max_added)
        for patch in patches:      # the restore points are taken before the first write, from what was just read
            self._restore_points.setdefault(patch.name, live[patch.name])
        self._affected = [patch.name for patch in patches]
        for patch in patches:
            self._gateway.patch_deployment(patch.document, expect_resource_version=live[patch.name].resource_version)

    def _wait(self, done: Callable[[], bool], timeout_s: float) -> bool:
        deadline = self._clock() + timeout_s
        while True:
            if done():
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(self._poll_s)

    def _services_settled(self, names: list[str]) -> bool:
        if names and not self._gateway.rollout_complete(self.namespace, names):
            return False
        graph = self._gateway.read_graph(self.namespace)
        affected = [svc for svc in graph.services if not names or set(svc.deployment_names) & set(names)]
        return bool(affected) and all(
            pod.ready and (pod.phase or "Running") != "Pending" and svc.ready_replicas == svc.declared_replicas
            for svc in affected for pod in svc.pods)

    def await_settled(self) -> bool:
        return self._wait(lambda: self._services_settled(self._affected), self._settle_s)

    def fail_node(self, node: str) -> DeploymentGraph:
        self._require_writes()
        self._assert_same_cluster()
        require_node_failure(self._injector)
        known = {n.name: n for n in self.baseline_graph.nodes}
        if node not in known or not _is_worker(known[node]):
            raise ValidationRefusedError("NODE_NOT_ELIGIBLE", f"{node!r} is not a worker node of the baseline.")
        if self._gateway.node_ready(node) is not True:
            raise ValidationRefusedError("NODE_NOT_ELIGIBLE", f"{node!r} is not Ready now, so failing it proves nothing.")

        self._injector.inject(node)
        if not self._wait(lambda: self._gateway.node_ready(node) is False, self._detect_s):
            raise RuntimeError(f"node {node} did not become NotReady within {self._detect_s:.0f}s of the stop: the failure "
                               f"was not produced, so nothing was measured")

        def pods_unready() -> bool:
            graph = self._gateway.read_graph(self.namespace)
            return all(not pod.ready for svc in graph.services for pod in svc.pods if pod.node == node)

        if not self._wait(pods_unready, self._unready_s):
            raise RuntimeError(f"pods on {node} were still Ready {self._unready_s:.0f}s after the node went NotReady, so "
                               f"the effect of the failure cannot be observed")
        return self._gateway.read_graph(self.namespace)

    def restore_node(self, node: str) -> None:
        self._injector.restore(node)
        if not self._wait(lambda: self._gateway.node_ready(node) is True, self._restore_s):
            raise RuntimeError(f"node {node} did not become Ready within {self._restore_s:.0f}s of being started")

    def revert(self) -> None:
        if not self._restore_points:
            return
        self._assert_same_cluster()
        for name in reversed(list(self._restore_points)):
            self._gateway.restore_deployment(self._restore_points[name])
            del self._restore_points[name]      # forgotten only once the API accepted it
        names = list(self._affected)
        if not self._wait(lambda: self._gateway.rollout_complete(self.namespace, names), self._settle_s):
            raise RuntimeError(f"the Deployments {', '.join(names)} did not settle within {self._settle_s:.0f}s of being "
                               f"put back")
