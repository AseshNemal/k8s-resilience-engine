"""Wire the pieces of a real-cluster validation together, read-only unless a person's write approval is supplied.

This is the one module that imports both the engine (the executor, the probe, the authorizations) and the integrations (the
gateway, the kind injector, the HTTP fetch), so neither layer imports the other. It performs no experiment. It builds the
stack, and building it reads the test cluster (its identity and its baseline) because the executor refuses to exist
otherwise; that is all it does to the cluster.

Writes. By default the gateway is built read-only and the executor is not armed, so a run through this stack can neither patch a
Deployment nor stop a node. Both are switched on together, and only by a ``WriteApproval``: a named person's statement of what was
approved, for the cluster whose ``kube-system`` UID the designation pins. An approval for another cluster is refused. That is
the only door; there is no flag that enables one layer without the other.

Stopping a node is separate again: the kind injector refuses to act until it has been armed with a ``STOP_KIND_WORKER_CONTAINER``
authorization bound to the plan, once for each kill (``arm_stop_authorizations``).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.engine.deployment_topology_modeling.discovery import discover
from app.engine.recommendation_evaluation_validation.validation import (
    STOP_KIND_WORKER,
    DisposableClusterDesignation,
    ValidationPlan,
    authorize,
)
from app.engine.recommendation_evaluation_validation.validation.kubernetes_executor import KubernetesValidationExecutor
from app.engine.recommendation_evaluation_validation.validation.workflow_probe import ProbeSpec, WorkflowProbe
from app.integrations.kind.node_fault import KindWorkerFaultInjector
from app.integrations.kind.recovery import Runner, subprocess_runner
from app.integrations.kubernetes.validation_gateway import KubernetesTestClusterGateway
from app.integrations.probe.http_fetch import LoopbackHttpFetch

REAL, DOUBLE = "REAL_CLUSTER", "TEST_DOUBLE"


@dataclass(frozen=True)
class WriteApproval:
    """A person's explicit approval to let a run change the test cluster, for that cluster only."""

    confirmed_by: str
    cluster_uid: str
    statement: str          # what was approved, in the person's own words

    def valid_for(self, designation: DisposableClusterDesignation) -> bool:
        return (bool((self.confirmed_by or "").strip()) and bool((self.statement or "").strip())
                and self.cluster_uid == designation.cluster_uid)


@dataclass(frozen=True)
class StackConfig:
    kubeconfig_path: str
    designation: DisposableClusterDesignation
    kind_cluster: str
    workers: tuple[str, ...]
    recovery_dir: str
    probe: ProbeSpec | None = None
    write_approval: WriteApproval | None = None


class ProbeFactory:
    """Makes one probe per failure episode. It says whether its requests are real, so the record can."""

    def __init__(self, spec: ProbeSpec, transport: httpx.BaseTransport | None, clock: Callable[[], float]) -> None:
        self.spec, self._fetch, self._clock = spec, LoopbackHttpFetch(transport=transport), clock
        self.evidence_class = REAL if transport is None else DOUBLE

    def __call__(self) -> WorkflowProbe:
        return WorkflowProbe(self.spec, self._fetch, clock=self._clock)


@dataclass
class Stack:
    config: StackConfig
    gateway: KubernetesTestClusterGateway
    injector: KindWorkerFaultInjector
    executor: KubernetesValidationExecutor

    @property
    def writes_enabled(self) -> bool:
        return self.gateway.allow_writes and self.executor.writes_enabled


def build_stack(config: StackConfig, *, runner: Runner = subprocess_runner, transport: httpx.BaseTransport | None = None,
                probe_transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.monotonic, executor_options: dict[str, Any] | None = None) -> Stack:
    approval = config.write_approval
    if approval is not None and not approval.valid_for(config.designation):
        raise ValueError("the write approval does not name a person, a statement, and the designated cluster's kube-system UID")
    writes = approval is not None
    gateway = KubernetesTestClusterGateway(config.kubeconfig_path, topology_reader=discover, allow_writes=writes,
                                           transport=transport)
    injector = KindWorkerFaultInjector(config.kind_cluster, config.workers, config.recovery_dir, runner=runner)
    options = dict(executor_options or {})
    if config.probe is not None:
        options["probe_factory"] = ProbeFactory(config.probe, probe_transport, clock)
    executor = KubernetesValidationExecutor(gateway, injector, config.designation, writes_enabled=writes, sleep=sleep,
                                            clock=clock, **options)
    return Stack(config, gateway, injector, executor)


def arm_stop_authorizations(stack: Stack, plan: ValidationPlan, confirmed_by: str, *, count: int) -> None:
    """Arm the kind injector for ``count`` kills of the plan's failed node (two when the baseline is measured)."""
    for _ in range(count):
        stack.injector.arm(authorize(plan, STOP_KIND_WORKER, confirmed_by), plan_digest=plan.digest,
                           target=plan.target.name, failed_node=plan.predicted.failed_node)
