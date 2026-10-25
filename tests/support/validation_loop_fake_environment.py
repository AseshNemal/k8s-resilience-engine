"""A fake ``ProofEnvironment`` over the in-memory test cluster, for the validation-loop service and API tests (TEST-ONLY).

It touches no cluster, no Docker and no network. Its recommendation is computed by Member 4's real ``evaluate_recommendation``
over the hand-written Bookinfo-shaped scenario, relabelled as live the way ``tests/support/member4_approvals.py`` does it, so
planning, ranking, YAML generation and per-worker prediction are the real code. The product never imports this module.
"""

from collections.abc import Callable
from typing import Any

from app.engine.recommendation_evaluation_validation.validation_loop.environment import (
    DISPOSABLE,
    DOCKER_DESKTOP,
    LoadedTarget,
    ProbeDefinition,
    TargetIdentity,
)
from app.engine.recommendation_evaluation_validation.validation_loop.fault_readiness import FaultReadiness, WorkerReadiness
from tests.support.member4_approvals import as_live
from tests.support.member4_harness import bookinfo_scenario
from tests.support.member4_test_cluster import FakeTestCluster

NAMESPACE = "bookinfo"


class FakeRecoverer:
    def __init__(self, env: "FakeEnvironment") -> None:
        self.env = env

    def restore_workers(self):
        self.env.recoveries.append("restore_workers")
        for node in list(self.env.cluster.down):
            self.env.cluster.down.discard(node)
        return [{"container": w, "restored": True, "error": None} for w in self.env.stopped_by_recovery]

    def revert_workloads(self, restore_points):
        self.env.recoveries.append(("revert_workloads", restore_points))
        self.env.cluster.revert()
        return {"reverted": True, "error": None}


class FakeEnvironment:
    def __init__(self, *, kind: str = DISPOSABLE, name: str = "kind-test", uid: str | None = "uid-1", cluster_options: dict | None = None,
                 extra_pods: list[dict[str, Any]] | None = None, probe_survives: bool | None = True, desktop_ack: bool | None = None,
                 readiness: FaultReadiness | None = None, layout: str = "concentrated", scenario_options: dict | None = None) -> None:
        self.kind, self.name, self.uid = kind, name, uid
        self.scenario = bookinfo_scenario(layout, **(scenario_options or {}))
        self.cluster = FakeTestCluster(self.scenario.deployment, name=name, **(cluster_options or {}))
        self.extra_pods = extra_pods if extra_pods is not None else [
            {"namespace": "kube-system", "name": "coredns-abc-1", "node": "w1", "ready": True, "ownerKind": "ReplicaSet", "ownerName": "coredns-abc12"},
            {"namespace": "kube-system", "name": "coredns-abc-2", "node": "w2", "ready": True, "ownerKind": "ReplicaSet", "ownerName": "coredns-abc12"}]
        self.probe_survives, self.readiness = probe_survives, readiness
        self._desktop_ack = (kind == DOCKER_DESKTOP) if desktop_ack is None else desktop_ack
        self.armed: list[tuple[str, str]] = []
        self.recoveries: list[Any] = []
        self.stopped_by_recovery = ["w1"]
        self.restore_points_seen: list[dict] = []

    def identity(self) -> TargetIdentity:
        return TargetIdentity(self.kind, self.name, self.uid, "https://127.0.0.1:6443", tuple(n.name for n in self.cluster.graph.nodes))

    def load(self, workflow_id: str, namespace: str) -> LoadedTarget:
        live = as_live(self.scenario.evaluate())
        return LoadedTarget(self.scenario.workflow, "FROZEN", live, self.cluster.graph.model_copy(deep=True))

    def eligible_workers(self, graph) -> list[str]:
        return sorted(n.name for n in graph.nodes if n.ready is not False)

    def read_graph(self, namespace: str):
        return self.cluster.graph.model_copy(deep=True)

    def read_pods(self) -> list[dict[str, Any]]:
        app_pods = [{"namespace": NAMESPACE, "name": p.name, "node": p.node, "ready": p.ready, "ownerKind": "ReplicaSet",
                     "ownerName": s.name + "-abc12"} for s in self.cluster.graph.services for p in s.pods]
        return app_pods + list(self.extra_pods)

    def fault_readiness(self, workers: list[str], window_verified: bool):
        return self.readiness

    def probe_check(self, probe: ProbeDefinition) -> dict[str, Any]:
        return {"samples": 3, "passed": 3 if self.probe_survives else 0, "survives": self.probe_survives,
                "reasons": [] if self.probe_survives else ["status 503, expected 200"]}

    def executor_factory(self, grant, authority, probe, on_restore_points: Callable[[dict], None]):
        def factory(candidate_id):
            on_restore_points({"reviews": {"replicas": 3}})
            self.restore_points_seen.append({"candidate": candidate_id})
            return self.cluster
        return factory

    def arm(self, grant, authority):
        return lambda authorization, worker, digest: self.armed.append((worker, authorization.action))

    def node_ready(self, worker: str):
        return worker not in self.cluster.down

    def recoverer(self, run):
        return FakeRecoverer(self)

    def requires_desktop_acknowledgement(self) -> bool:
        return self._desktop_ack


def readiness(*, window: str = "OK", cluster: str = "desktop", verified: bool = False) -> FaultReadiness:
    workers = tuple(WorkerReadiness(w, True, True, "no" if window == "OK" else "on-failure", window, None) for w in ("w1", "w2", "w3"))
    return FaultReadiness(cluster, workers, verified)
