"""The composition root of the validation loop: the real gateway, executor, kind injector, Docker runner and probe, built for one cluster.

This is the one place that imports both the engine (the loop, the executor, the authorizations) and the integrations (the Kubernetes
gateway, the kind injector, the Docker runner, the HTTP fetch), so neither layer imports the other. It performs no experiment: it
builds the pieces a run needs and hands them to the service.

Two clusters can be built here, with the same code:

* ``DOCKER_DESKTOP``: the cluster the extension is connected to (``desktop``, a kind cluster behind Docker Desktop). Its running
  workloads are used as they are; nothing is copied or redeployed. It is blocked by default at three independent places: the
  executor (``RESEARCH_CLUSTER_REFUSED``), the kind injector (``CLUSTER_REFUSED``) and the Docker proxy (its allow-list). Each opens
  only for an explicit ``ResearchClusterAuthorization`` that names this cluster's UID, which exists only when the person ticked the
  acknowledgement in the run approval.
* ``DISPOSABLE``: a separate kind cluster with credentials of its own, prepared by the extension.

Docker is reached only through the restricted proxy (``DockerEngineRunner``); this process has no Docker socket and no ``docker``
binary. A runner that is not the proxy's marks every record it produces as a test double.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.engine.deployment_topology_modeling.discovery import discover
from app.engine.failure_impact_prediction.runtime_provider import get_runtime_component_inputs
from app.engine.recommendation_evaluation_validation.declared_workflow import get_declared_workflow
from app.engine.recommendation_evaluation_validation.live_pipeline import (
    build_declared_recommendation,
    build_live_recommendation,
)
from app.engine.recommendation_evaluation_validation.validation.cluster_identity import DisposableClusterDesignation
from app.engine.recommendation_evaluation_validation.validation.kubernetes_executor import KubernetesValidationExecutor
from app.engine.recommendation_evaluation_validation.validation.models import Authorization
from app.engine.recommendation_evaluation_validation.validation.workflow_probe import ProbeSpec, WorkflowProbe
from app.engine.recommendation_evaluation_validation.validation_loop.environment import (
    DISPOSABLE,
    DOCKER_DESKTOP,
    LoadedTarget,
    ProbeDefinition,
    TargetIdentity,
)
from app.engine.recommendation_evaluation_validation.validation_loop.fault_readiness import FaultReadiness, assess_workers
from app.engine.recommendation_evaluation_validation.validation_loop.grant import GrantAuthority, RunGrant
from app.integrations.docker.engine import DockerEngineRunner
from app.integrations.kind.node_fault import KindWorkerFaultInjector
from app.integrations.kind.recovery import DockerCli, Runner, restore_pending
from app.integrations.kubernetes.validation_gateway import KubernetesTestClusterGateway, ObservedDeployment
from app.integrations.probe.http_fetch import LoopbackHttpFetch
from app.shared.schemas.deployment import DeploymentGraph

REAL, DOUBLE = "REAL_CLUSTER", "TEST_DOUBLE"
PROBE_SAMPLES = 3


@dataclass(frozen=True)
class ClusterConfig:
    kind: str                       # DOCKER_DESKTOP | DISPOSABLE
    name: str                       # the TestTarget / designation name
    kind_cluster: str               # the kind cluster whose workers are stopped: "desktop" | "resilience-validation"
    kubeconfig_path: str
    connected_cluster: bool         # True only for the cluster the extension itself is connected to
    recovery_dir: str


class ProbeFactory:
    """One probe per failure episode; says whether its requests are real so the record can."""

    def __init__(self, spec: ProbeSpec, transport: httpx.BaseTransport | None, clock: Callable[[], float]) -> None:
        self.spec, self._fetch, self._clock = spec, LoopbackHttpFetch(transport=transport), clock
        self.evidence_class = REAL if transport is None else DOUBLE

    def __call__(self) -> WorkflowProbe:
        return WorkflowProbe(self.spec, self._fetch, clock=self._clock)


def probe_spec(definition: ProbeDefinition, workflow_id: str, *, min_samples: int = 5) -> ProbeSpec:
    return ProbeSpec(workflow_id=workflow_id, url=definition.url, expected_status=definition.expected_status,
                     required_text=definition.must_contain, forbidden_text=definition.must_not_contain, min_samples=min_samples)


def connected_loader(workflow_basis: str) -> Callable[[str, str, KubernetesTestClusterGateway], LoadedTarget]:
    """Evaluate on the connected cluster with Member 3's and Member 2's public services (Docker Desktop: the target IS that cluster)."""
    def load(workflow_id: str, namespace: str, gateway: KubernetesTestClusterGateway) -> LoadedTarget:
        if workflow_basis == "DECLARED":
            workflow = get_declared_workflow(workflow_id).graph
            live = build_declared_recommendation(workflow_id, None, namespace)
        else:
            workflow = get_runtime_component_inputs(workflow_id, namespace).workflow
            live = build_live_recommendation(workflow_id, None, namespace)
        return LoadedTarget(workflow, workflow_basis, live, gateway.read_graph(namespace))
    return load


class ClusterRecoverer:
    """Recovery for runs of one cluster: start the workers that were stopped, put back the Deployments. It cannot stop or write a candidate."""

    def __init__(self, env: "ClusterProofEnvironment") -> None:
        self._env = env

    def restore_workers(self) -> list[dict[str, Any]]:
        outcome = restore_pending(Path(self._env.config.recovery_dir), DockerCli(self._env.runner), cluster=self._env.config.kind_cluster)
        return ([{"container": name, "restored": True, "error": None} for name in outcome["restored"]]
                + [{"container": name, "restored": False, "error": "docker start failed or the container does not report running"}
                   for name in outcome["failed"]])

    def revert_workloads(self, restore_points: dict[str, Any] | None) -> dict[str, Any]:
        if not restore_points:
            return {"reverted": None, "error": None}
        gateway = self._env.writing_gateway()
        snapshots = [_snapshot(data) for data in restore_points.values()]
        for snapshot in snapshots:
            gateway.restore_deployment(snapshot)
        namespace = snapshots[0].namespace
        deadline = time.monotonic() + self._env.recovery_timeout_s
        names = [s.name for s in snapshots]
        while time.monotonic() < deadline:
            if gateway.rollout_complete(namespace, names):
                return {"reverted": True, "error": None}
            self._env.sleep(self._env.poll_s)
        return {"reverted": False, "error": "the Deployments were patched back but the rollout did not complete in time"}


def _snapshot(data: dict[str, Any]) -> ObservedDeployment:
    fields = dict(data)
    fields["topology_spread_constraints"] = tuple(fields.get("topology_spread_constraints") or ())
    return ObservedDeployment(**fields)


class ClusterProofEnvironment:
    """A ``ProofEnvironment`` over a real cluster, a real kind injector and the restricted Docker proxy."""

    def __init__(self, config: ClusterConfig, *, loader: Callable[[str, str, KubernetesTestClusterGateway], LoadedTarget] | None = None,
                 runner: Runner | None = None, transport: httpx.BaseTransport | None = None,
                 probe_transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, executor_options: dict[str, Any] | None = None,
                 recovery_timeout_s: float = 300.0, poll_s: float = 2.0, workflow_basis: str = "FROZEN") -> None:
        self.config, self.kind = config, config.kind
        self.runner: Runner = runner or DockerEngineRunner()
        self._transport, self._probe_transport = transport, probe_transport
        self.sleep, self._clock, self._executor_options = sleep, clock, dict(executor_options or {})
        self.recovery_timeout_s, self.poll_s = recovery_timeout_s, poll_s
        self._loader = loader or connected_loader(workflow_basis)
        self._injector: KindWorkerFaultInjector | None = None
        self._injector_run: str | None = None

    # -- reading -------------------------------------------------------------------------------------------------

    def _gateway(self, *, writes: bool) -> KubernetesTestClusterGateway:
        return KubernetesTestClusterGateway(self.config.kubeconfig_path, topology_reader=discover, allow_writes=writes,
                                            connected_cluster=self.config.connected_cluster, transport=self._transport)

    def writing_gateway(self) -> KubernetesTestClusterGateway:
        return self._gateway(writes=True)

    def identity(self) -> TargetIdentity:
        observed = self._gateway(writes=False).identity()
        return TargetIdentity(self.kind, self.config.name, observed.cluster_uid, observed.api_server, tuple(observed.node_names))

    def load(self, workflow_id: str, namespace: str) -> LoadedTarget:
        return self._loader(workflow_id, namespace, self._gateway(writes=False))

    def eligible_workers(self, graph: DeploymentGraph) -> list[str]:
        pattern = re.compile(rf"^{re.escape(self.config.kind_cluster)}-worker[0-9]*$")
        return sorted(n.name for n in graph.nodes if pattern.match(n.name) and n.ready is True)

    def read_graph(self, namespace: str) -> DeploymentGraph:
        return self._gateway(writes=False).read_graph(namespace)

    def read_pods(self) -> list[dict[str, Any]]:
        return self._gateway(writes=False).read_all_pods()

    def node_ready(self, worker: str) -> bool | None:
        return self._gateway(writes=False).node_ready(worker)

    def fault_readiness(self, workers: list[str], window_verified: bool) -> FaultReadiness | None:
        return assess_workers(DockerCli(self.runner), self.config.kind_cluster, workers, window_verified=window_verified)

    def requires_desktop_acknowledgement(self) -> bool:
        return self.kind == DOCKER_DESKTOP

    # -- the workflow check ----------------------------------------------------------------------------------------

    def probe_check(self, probe: ProbeDefinition) -> dict[str, Any]:
        """Three GET requests through the loopback-only fetch, against the cluster as it is now. Sends nothing else."""
        spec = probe_spec(probe, "probe-test", min_samples=1)
        workflow_probe = WorkflowProbe(spec, LoopbackHttpFetch(transport=self._probe_transport), clock=self._clock)
        samples = []
        for _ in range(PROBE_SAMPLES):
            samples.append(workflow_probe.sample())
            self.sleep(0.5)
        passed = sum(1 for s in samples if s.ok)
        reasons = sorted({s.reason for s in samples if s.reason})
        return {"samples": len(samples), "passed": passed, "survives": passed == len(samples), "reasons": reasons,
                "evidenceClass": REAL if self._probe_transport is None else DOUBLE}

    # -- running ---------------------------------------------------------------------------------------------------

    def _injector_for(self, grant: RunGrant, authority: GrantAuthority) -> KindWorkerFaultInjector:
        if self._injector is None or self._injector_run != grant.run_id:
            research = authority.research_authorization(grant.cluster_uid) if self.kind == DOCKER_DESKTOP else None
            self._injector = KindWorkerFaultInjector(self.config.kind_cluster, grant.workers, self.config.recovery_dir, runner=self.runner,
                                                     research_authorization=research, research_cluster_uid=grant.cluster_uid)
            self._injector_run = grant.run_id
        return self._injector

    def executor_factory(self, grant: RunGrant, authority: GrantAuthority, probe: ProbeDefinition | None,
                         on_restore_points: Callable[[dict[str, Any]], None]):
        injector = self._injector_for(grant, authority)
        research = authority.research_authorization(grant.cluster_uid) if self.kind == DOCKER_DESKTOP else None

        def factory(candidate_id: str | None) -> KubernetesValidationExecutor:
            gateway = self._gateway(writes=True)
            identity = gateway.identity()
            designation = DisposableClusterDesignation(name=self.config.name, api_server=identity.api_server or "",
                                                       cluster_uid=grant.cluster_uid, namespace=grant.namespace,
                                                       designated_by=grant.authorizer)
            options = dict(self._executor_options)
            if probe is not None:
                options["probe_factory"] = ProbeFactory(probe_spec(probe, "validation-loop"), self._probe_transport, self._clock)
            return KubernetesValidationExecutor(gateway, injector, designation, research_override=research, writes_enabled=True,
                                                sleep=self.sleep, clock=self._clock, on_restore_points=on_restore_points, **options)
        return factory

    def arm(self, grant: RunGrant, authority: GrantAuthority) -> Callable[[Authorization, str, str], None]:
        injector = self._injector_for(grant, authority)

        def arm(authorization: Authorization, worker: str, plan_digest: str) -> None:
            injector.arm(authorization, plan_digest=plan_digest, target=grant.target_name, failed_node=worker)
        return arm

    def recoverer(self, run: dict[str, Any]) -> ClusterRecoverer:
        return ClusterRecoverer(self)


def docker_desktop_environment(kubeconfig_path: str, recovery_dir: str, **options: Any) -> ClusterProofEnvironment:
    """The Docker Desktop research cluster, as the extension is connected to it. Blocked until a run approval acknowledges it."""
    return ClusterProofEnvironment(ClusterConfig(DOCKER_DESKTOP, "docker-desktop-research", "desktop", kubeconfig_path, True, recovery_dir),
                                   **options)


def disposable_environment(kubeconfig_path: str, recovery_dir: str, **options: Any) -> ClusterProofEnvironment:
    return ClusterProofEnvironment(ClusterConfig(DISPOSABLE, "resilience-validation", "resilience-validation", kubeconfig_path, False,
                                                 recovery_dir), **options)
