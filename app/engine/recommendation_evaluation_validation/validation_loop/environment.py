"""What the validation loop needs from the cluster it proves a change on, as one small interface.

Two environments exist: the Docker Desktop research cluster (the connected cluster; its running workloads are used as they are) and
a disposable kind cluster. Everything target-specific lives behind ``ProofEnvironment``, so planning, approval, the run and the
page are the same for both, and the whole of it is testable with a fake environment that touches no cluster.

An environment never decides anything about safety. The loop, the grant, the disruption guard and the executor do. An environment
only reads, builds those pieces for its own cluster, and says honestly what it cannot do.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.engine.recommendation_evaluation_validation.approval import LiveRecommendation
from app.engine.recommendation_evaluation_validation.validation.models import Authorization
from app.engine.recommendation_evaluation_validation.validation_loop.fault_readiness import FaultReadiness
from app.engine.recommendation_evaluation_validation.validation_loop.grant import GrantAuthority, RunGrant
from app.engine.recommendation_evaluation_validation.validation_loop.loop import ExecutorFactory
from app.engine.recommendation_evaluation_validation.validation_loop.worker import Recoverer
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.workflow import WorkflowGraph

DOCKER_DESKTOP, DISPOSABLE = "DOCKER_DESKTOP", "DISPOSABLE"


@dataclass(frozen=True)
class TargetIdentity:
    """Who the target says it is. ``cluster_uid`` None means it could not be read, which refuses any run."""

    kind: str
    name: str
    cluster_uid: str | None
    api_server: str | None
    node_names: tuple[str, ...]


@dataclass(frozen=True)
class ProbeDefinition:
    """The developer's answer to "how do we know the workflow works": one GET and what the page must and must not say."""

    url: str
    expected_status: int = 200
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "expectedStatus": self.expected_status, "mustContain": list(self.must_contain),
                "mustNotContain": list(self.must_not_contain)}


@dataclass
class LoadedTarget:
    """The workflow and the recommendation computed on THIS target's own topology (never carried over from another cluster)."""

    workflow: WorkflowGraph
    workflow_basis: str                         # FROZEN | DECLARED
    live: LiveRecommendation                    # evaluated on the target, for its riskiest worker
    graph: DeploymentGraph
    notes: list[str] = field(default_factory=list)


class ProofEnvironment(Protocol):
    kind: str

    def identity(self) -> TargetIdentity: ...

    def load(self, workflow_id: str, namespace: str) -> LoadedTarget:
        """Read the target and evaluate the recommendation on it. Refuses with a coded error when it cannot (nothing is faked)."""

    def eligible_workers(self, graph: DeploymentGraph) -> list[str]:
        """Worker nodes that may be taken down: Ready workers, never a control plane."""

    def read_graph(self, namespace: str) -> DeploymentGraph: ...

    def read_pods(self) -> list[dict[str, Any]]: ...

    def fault_readiness(self, workers: list[str], window_verified: bool) -> FaultReadiness | None:
        """Can the worker containers be identified and stopped for a valid window? None when this environment cannot say."""

    def probe_check(self, probe: ProbeDefinition) -> dict[str, Any]:
        """Three samples of the workflow check against the unchanged deployment: ``{"samples", "passed", "survives", "reasons"}``."""

    def executor_factory(self, grant: RunGrant, authority: GrantAuthority, probe: ProbeDefinition | None,
                         on_restore_points: Callable[[dict[str, Any]], None]) -> ExecutorFactory: ...

    def arm(self, grant: RunGrant, authority: GrantAuthority) -> Callable[[Authorization, str, str], None] | None:
        """How a minted kill authorization reaches the fault injector (None when the environment's injector needs none)."""

    def node_ready(self, worker: str) -> bool | None: ...

    def recoverer(self, grant_or_run: dict[str, Any]) -> Recoverer: ...

    def requires_desktop_acknowledgement(self) -> bool:
        """True for the Docker Desktop research cluster: the run approval must carry the explicit acknowledgement."""
