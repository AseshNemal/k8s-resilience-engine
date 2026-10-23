"""The only door from a validation run to a cluster: what a ``ClusterGateway`` must offer, and nothing more.

The executor (``kubernetes_executor``) holds all the safety logic: who the cluster is, what may be written, in which
order, what is restored. A gateway only does the contact. That split is what makes the safety logic testable without a
cluster, and it keeps the cluster client out of the research code: a gateway for a real cluster lives with the other
integrations (``app/integrations``) and satisfies this protocol structurally, importing nothing from the engine.

No gateway for a real cluster exists yet. The only implementation is the in-memory double in ``tests/support``.

What a gateway must never do: fall back to the credentials the backend itself uses, or write anything the executor did not ask
for. It is constructed with credentials that were named for this purpose and nothing else.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.engine.recommendation_evaluation_validation.validation.cluster_identity import ClusterIdentity
from app.shared.schemas.deployment import DeploymentGraph

HOSTNAME = "kubernetes.io/hostname"
# Where the evidence came from. Only REAL_CLUSTER can ever be presented as an experimental observation.
REAL_CLUSTER, TEST_DOUBLE = "REAL_CLUSTER", "TEST_DOUBLE"


@dataclass(frozen=True)
class LiveDeployment:
    """A Deployment as it is in the cluster right now, reduced to what a placement change can touch."""

    name: str
    namespace: str
    resource_version: str
    generation: int
    replicas: int
    selector: Mapping[str, str]                   # spec.selector.matchLabels
    pod_labels: Mapping[str, str]                 # spec.template.metadata.labels
    topology_spread_constraints: tuple[dict[str, Any], ...] = ()
    pod_anti_affinity: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)      # gateway-specific detail, never read by the executor

    @property
    def has_hostname_spread(self) -> bool:
        return any(c.get("topologyKey") == HOSTNAME for c in self.topology_spread_constraints)

    @property
    def has_required_anti_affinity(self) -> bool:
        return bool((self.pod_anti_affinity or {}).get("requiredDuringSchedulingIgnoredDuringExecution"))


class ClusterGateway(Protocol):
    """Contact with one cluster. Every method is a single, narrow operation; none of them decides anything."""

    evidence_class: str        # REAL_CLUSTER or TEST_DOUBLE

    def identity(self) -> ClusterIdentity:
        """Who the connected cluster says it is (kube-system namespace UID, API address, context, node names)."""

    def read_graph(self, namespace: str) -> DeploymentGraph:
        """The placement and readiness of the experiment namespace's pods right now. Read only, never cached."""

    def read_deployments(self, namespace: str) -> Mapping[str, LiveDeployment]:
        """The Deployments of the namespace by name. Read only."""

    def patch_deployment(self, document: dict[str, Any], *, expect_resource_version: str) -> None:
        """Strategic-merge ``document`` into its Deployment; fails if the Deployment's version is not the expected one."""

    def restore_deployment(self, snapshot: LiveDeployment) -> None:
        """Put the replica count, the spread constraints and the pod anti-affinity back to what ``snapshot`` holds."""

    def rollout_complete(self, namespace: str, names: Sequence[str]) -> bool:
        """True when each named Deployment has observed its latest generation and has all replicas updated and available."""

    def node_ready(self, node: str) -> bool | None:
        """The node's Ready condition; None when the cluster does not report it."""
