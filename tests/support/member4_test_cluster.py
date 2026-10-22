"""An in-memory stand-in for a TEST cluster, to exercise Member 4's validation run without any cluster.

TEST-ONLY. It implements ``ValidationExecutor`` over a ``DeploymentGraph`` held in memory. "Applying" a YAML proposal
re-places the pods of the Deployments it names using a scripted scheduler, and "failing a node" marks the pods on it not
ready. Nothing here opens a connection, reads a kubeconfig or runs a command.

The scheduler is a separate, deliberately simple policy, not the simulator under test, so a scenario can make the
"observed" placement agree with the prediction (``spread``), differ from it (``stay``, ``pack``), or never settle.
``calls`` records every contact so a test can prove that a refused step wrote nothing.
"""

from collections import Counter
from collections.abc import Callable

import yaml

from app.engine.recommendation_evaluation_validation.validation import TestTarget, ValidationInterrupted
from app.shared.schemas.deployment import DeploymentGraph

Scheduler = Callable[[DeploymentGraph, list[str]], None]


def spread(graph: DeploymentGraph, services: list[str]) -> None:
    """Least-loaded node first, ties by name: what a working DoNotSchedule/maxSkew 1 constraint tends to produce."""
    names = sorted(n.name for n in graph.nodes)
    for service in graph.services:
        if service.name not in services:
            continue
        load = Counter({name: 0 for name in names})
        for pod in service.pods:
            node = min(names, key=lambda n: (load[n], n))
            load[node] += 1
            pod.node = node


def stay(graph: DeploymentGraph, services: list[str]) -> None:
    """The scheduler puts the replacements exactly where the old pods were: the constraint changed nothing."""


def pack(node: str) -> Scheduler:
    def schedule(graph: DeploymentGraph, services: list[str]) -> None:
        for service in graph.services:
            if service.name in services:
                for pod in service.pods:
                    pod.node = node
    return schedule


class FakeTestCluster:
    """The ``ValidationExecutor`` double. ``environment`` defaults to ``test``; pass another to prove it is refused."""

    def __init__(self, graph: DeploymentGraph, *, name: str = "kind-test", environment: str = "test",
                 scheduler: Scheduler = spread, settles: bool = True, interrupt_at: str | None = None,
                 fail_at: str | None = None, restore_fails: bool = False) -> None:
        self.target = TestTarget(name, environment)
        self.graph = graph.model_copy(deep=True)
        self.scheduler, self.settles = scheduler, settles
        self.interrupt_at, self.fail_at, self.restore_fails = interrupt_at, fail_at, restore_fails
        self.calls: list[str] = []
        self.down: set[str] = set()

    @property
    def wrote(self) -> bool:
        return any(call.startswith(("apply", "fail:")) for call in self.calls)

    def _maybe_break(self, stage: str) -> None:
        if self.interrupt_at == stage:
            raise ValidationInterrupted(f"connection lost during {stage}")
        if self.fail_at == stage:
            raise RuntimeError(f"{stage} failed in the test cluster")

    def observe(self) -> DeploymentGraph:
        self.calls.append("observe")
        return self.graph.model_copy(deep=True)

    def apply(self, yaml_text: str) -> None:
        self.calls.append("apply")
        self._maybe_break("apply")
        documents = [doc for doc in yaml.safe_load_all(yaml_text) if doc and doc.get("kind") == "Deployment"]
        services = []
        for doc in documents:
            service = next((s for s in self.graph.services if doc["metadata"]["name"] in s.deployment_names), None)
            if service is None:
                continue
            services.append(service.name)
            replicas = (doc.get("spec") or {}).get("replicas")
            while replicas is not None and len(service.pods) < replicas:      # the controller creates the missing pods
                service.pods.append(service.pods[0].model_copy(update={"name": f"{service.name}-{len(service.pods)}"}))
            service.declared_replicas = len(service.pods)
            service.ready_replicas = sum(1 for pod in service.pods if pod.ready)
        self.scheduler(self.graph, services)

    def await_settled(self) -> bool:
        self.calls.append("await_settled")
        self._maybe_break("settle")
        return self.settles

    def fail_node(self, node: str) -> DeploymentGraph:
        self.calls.append(f"fail:{node}")
        self.down.add(node)
        self._maybe_break("failure")
        failed = self.graph.model_copy(deep=True)
        for service in failed.services:
            for pod in service.pods:
                if pod.node == node:
                    pod.ready = False
            service.ready_replicas = sum(1 for pod in service.pods if pod.ready)
        return failed

    def restore_node(self, node: str) -> None:
        self.calls.append(f"restore:{node}")
        if self.restore_fails:
            raise RuntimeError("could not bring the node back")
        self.down.discard(node)
