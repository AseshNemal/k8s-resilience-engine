"""What stopping a worker would take down besides the application under test, and the guard that refuses a kill because of it.

The Docker Desktop cluster is the developer's everyday cluster. Stopping one of its workers on purpose also stops everything else
scheduled there (DNS, a storage provisioner, someone's other application), and a test result obtained while the cluster's own
essential services are down says nothing about the application's placement. So before EVERY kill the guard reads the cluster
and decides, per workload that would lose a pod:

* BLOCK: an essential workload would become unavailable. That is a workload in a critical namespace (``kube-system`` and
  ``local-path-storage`` by default, extendable by configuration) none of whose pods would stay Ready on another node, a bare pod in
  such a namespace (nothing recreates it), or anything whose state cannot be read. A BLOCK is never bypassed by an acknowledgement.
* ACKNOWLEDGE: a workload outside the application's namespace and not critical would lose a pod. It is listed, and the kill proceeds
  only if the person acknowledged that workload when approving the run. The acknowledgement is by workload name, so a workload that
  appeared since the approval is not covered by it.
* REMAINS_AVAILABLE: an essential workload loses a pod but keeps Ready pods on other nodes. Reported, not blocked.
* NODE_LOCAL: a DaemonSet pod on the worker whose DaemonSet keeps Ready pods on other nodes. Losing the pod on the failed node is what a
  node failure is, so it is expected and not a finding. A DaemonSet whose only Ready pods are on this worker is NOT node-local: it
  is treated like any other workload (a BLOCK in a critical namespace).
* the application's own namespace is the experiment, not a disruption, and is judged by the loop.

It also refuses when the cluster is not healthy now: a node that is NotReady, or a pod of the application that is not Ready, means
the baseline for this experiment is not clean. Everything fails closed: a read that fails or returns something incomplete refuses.

Pure functions over facts the caller reads; the guard wraps them for the loop's ``before_kill`` hook.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.engine.recommendation_evaluation_validation.validation_loop.loop import KillBlocked
from app.shared.schemas.deployment import DeploymentGraph

DEFAULT_CRITICAL_NAMESPACES = ("kube-system", "local-path-storage")
BLOCK, ACKNOWLEDGE, NODE_LOCAL, REMAINS = "BLOCK", "ACKNOWLEDGE", "NODE_LOCAL", "REMAINS_AVAILABLE"


@dataclass(frozen=True)
class PodFact:
    namespace: str | None
    name: str | None
    node: str | None
    ready: bool | None
    owner_kind: str | None = None
    owner_name: str | None = None
    phase: str | None = None

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "PodFact":
        return cls(item.get("namespace"), item.get("name"), item.get("node"), item.get("ready"), item.get("ownerKind"),
                   item.get("ownerName"), item.get("phase"))

    def workload(self) -> tuple[str, str, str]:
        """(namespace, kind, name) of what recreates this pod. A ReplicaSet is attributed to its Deployment by name."""
        kind, name = self.owner_kind, self.owner_name
        if kind == "ReplicaSet" and name and "-" in name:
            return (self.namespace or "", "Deployment", name.rsplit("-", 1)[0])
        if kind and name:
            return (self.namespace or "", kind, name)
        return (self.namespace or "", "Pod", self.name or "")


@dataclass
class Finding:
    workload: str                  # "namespace/Kind/name"
    namespace: str
    severity: str                  # BLOCK | ACKNOWLEDGE | REMAINS_AVAILABLE | NODE_LOCAL
    pods_on_worker: int
    ready_elsewhere: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"workload": self.workload, "namespace": self.namespace, "severity": self.severity,
                "podsOnWorker": self.pods_on_worker, "readyElsewhere": self.ready_elsewhere, "reason": self.reason}


@dataclass
class DisruptionReport:
    worker: str
    findings: list[Finding] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)      # evidence gaps and health problems; each one refuses

    @property
    def blocks(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == BLOCK]

    @property
    def to_acknowledge(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ACKNOWLEDGE]

    def to_dict(self) -> dict[str, Any]:
        return {"worker": self.worker, "findings": [f.to_dict() for f in self.findings], "problems": list(self.problems),
                "blocked": bool(self.blocks or self.problems), "needsAcknowledgement": [f.workload for f in self.to_acknowledge]}


def analyse(worker: str, pods: list[PodFact], app_namespace: str, critical_namespaces: tuple[str, ...] = DEFAULT_CRITICAL_NAMESPACES) -> DisruptionReport:
    """Per workload with a pod on ``worker`` (outside the application's namespace): BLOCK, ACKNOWLEDGE or NODE_LOCAL."""
    report = DisruptionReport(worker)
    if not worker:
        report.problems.append("no worker was named")
        return report
    ready_elsewhere: dict[tuple[str, str, str], int] = defaultdict(int)
    on_worker: dict[tuple[str, str, str], list[PodFact]] = defaultdict(list)
    for pod in pods:
        if pod.node is None and pod.phase in (None, "Pending"):
            continue      # not scheduled anywhere: not on this worker, and not a replica that is up
        if pod.node == worker:
            on_worker[pod.workload()].append(pod)
        elif pod.ready is True:
            ready_elsewhere[pod.workload()] += 1

    critical = set(critical_namespaces)
    for workload, local in sorted(on_worker.items()):
        namespace, kind, name = workload
        if namespace == app_namespace:
            continue
        label = f"{namespace}/{kind}/{name}"
        elsewhere = ready_elsewhere.get(workload, 0)
        if any(pod.ready is None for pod in local) or not namespace:
            report.findings.append(Finding(label, namespace, BLOCK, len(local), elsewhere,
                                           "its state on this worker could not be read, so it is treated as essential"))
            continue
        is_critical = namespace in critical
        if kind == "Pod":
            severity = BLOCK if is_critical else ACKNOWLEDGE
            reason = "a bare pod: nothing recreates it" + (" and it is in a critical namespace" if is_critical else "")
        elif elsewhere == 0:
            severity = BLOCK if is_critical else ACKNOWLEDGE
            reason = ("no Ready pod would remain on another node" + (" for an essential service" if is_critical else ""))
        elif kind == "DaemonSet":
            severity, reason = NODE_LOCAL, "a node-local DaemonSet pod; Ready pods of it remain on other nodes"
        elif is_critical:
            # An essential service that keeps a Ready replica elsewhere stays available; it is reported, not blocked.
            severity, reason = REMAINS, f"{elsewhere} Ready pod(s) remain on other nodes"
        else:
            severity, reason = ACKNOWLEDGE, f"{len(local)} pod(s) would go down; {elsewhere} Ready elsewhere"
        report.findings.append(Finding(label, namespace, severity, len(local), elsewhere, reason))
    return report


def health_problems(graph: DeploymentGraph, namespace: str) -> list[str]:
    """Why the cluster is not a clean baseline right now (a node NotReady, a pod of the application not Ready)."""
    problems = [f"node {node.name} is not Ready" for node in graph.nodes if node.ready is not True]
    problems += [f"pod {pod.name} of the application is not Ready" for service in graph.services for pod in service.pods
                 if not pod.ready]
    return problems


def make_guard(*, read_pods: Callable[[], list[dict[str, Any]]], read_graph: Callable[[], DeploymentGraph], namespace: str,
               critical_namespaces: tuple[str, ...] = DEFAULT_CRITICAL_NAMESPACES, acknowledged: frozenset[str] = frozenset(),
               check_outside_namespace: bool = True) -> Callable[[str, str | None], None]:
    """The loop's ``before_kill`` hook: raises ``KillBlocked`` unless stopping ``worker`` is safe for everything else.

    Called before EVERY kill, so it reads the cluster each time. A read that fails refuses. ``acknowledged`` holds the
    ``namespace/Kind/name`` of the non-critical workloads the person accepted at approval; it never covers a BLOCK.
    """
    def guard(worker: str, candidate_id: str | None) -> None:
        try:
            graph = read_graph()
            pods = [PodFact.from_dict(item) for item in read_pods()] if check_outside_namespace else []
        except Exception as error:      # noqa: BLE001  (fail closed)
            raise KillBlocked("EVIDENCE_INCOMPLETE", f"the cluster could not be read before stopping {worker}: {error}") from error
        unhealthy = health_problems(graph, namespace)
        if unhealthy:
            raise KillBlocked("CLUSTER_NOT_HEALTHY", "; ".join(unhealthy[:4]))
        if not check_outside_namespace:
            return
        if not pods:
            raise KillBlocked("EVIDENCE_INCOMPLETE", "the pods of the cluster could not be listed")
        report = analyse(worker, pods, namespace, critical_namespaces)
        if report.problems:
            raise KillBlocked("EVIDENCE_INCOMPLETE", "; ".join(report.problems))
        if report.blocks:
            raise KillBlocked("ESSENTIAL_WORKLOAD", "stopping " + worker + " would make unavailable: "
                              + "; ".join(f"{f.workload} ({f.reason})" for f in report.blocks[:4]))
        missing = [f.workload for f in report.to_acknowledge if f.workload not in acknowledged]
        if missing:
            raise KillBlocked("ACKNOWLEDGEMENT_MISSING", "these workloads would go down and were not acknowledged in the "
                              "run approval: " + ", ".join(missing[:5]))

    return guard
