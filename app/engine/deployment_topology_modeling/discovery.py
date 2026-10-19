"""Live cluster discovery for Component 2 (Deployment Topology Modeling).

``discover()`` reads the connected cluster through a ``ClusterSource`` and returns a ``DiscoveryResult``:
the unchanged ``DeploymentGraph`` contract wrapped in an envelope that says how much of the cluster was
actually resolved. It either returns real evidence or raises ``DiscoveryError``; there is no fallback to
sample data, and nothing is invented to fill a gap.

Phase 1 limitations, stated rather than hidden:
* pods are attributed to deployments by ``app`` label == deployment name; Service selectors and
  EndpointSlices are not read yet, so unattributed pods are reported as unresolved, not guessed at;
* the namespace's existence cannot be verified (no Namespace read permission);
* the connection fingerprint detects a change of connection, it does not prove which cluster this is.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Literal, Protocol

from app.engine.deployment_topology_modeling.graph_builder import DeploymentGraphBuilder
from app.engine.deployment_topology_modeling.live_state.cluster_models import (
    ClusterDeployment,
    ClusterNode,
    ClusterPod,
)
from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import (
    analyze_replica_concentration,
)
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import (
    find_unattributed_pods,
    related_deployments,
    resolve_deployment_ownership,
)
from app.integrations.kubernetes.client import (
    AuthFailedError,
    ClusterAccessError,
    ConnectionFailedError,
    ForbiddenError,
    KubernetesApiSource,
    NotConnectedError,
)
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.base import APIModel
from app.shared.schemas.deployment import DeploymentGraph

logger = logging.getLogger(__name__)

COMPONENT = "deployment_topology"
NAMESPACE_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


class ClusterSource(Protocol):
    def list_nodes(self) -> list[ClusterNode]: ...
    def list_pods(self, namespace: str) -> list[ClusterPod]: ...
    def list_deployments(self, namespace: str) -> list[ClusterDeployment]: ...
    def connection_fingerprint(self) -> str | None: ...


def get_cluster_source() -> ClusterSource:
    """The single place a real cluster connection is created. Tests replace it with a fake."""
    return KubernetesApiSource()


class DiscoveryError(EvidenceError):
    def __init__(self, code: str, message: str, *, http_status: int, retryable: bool = False,
                 details: dict | None = None) -> None:
        super().__init__(code, message, http_status=http_status, component=COMPONENT,
                         retryable=retryable, details=details)


class DiscoveryWarning(APIModel):
    code: str
    message: str


class UnresolvedItem(APIModel):
    kind: str
    name: str
    reason: str
    namespace: str
    affects: list[str] = []


class ServiceEvidence(APIModel):
    complete: bool
    reasons: list[str] = []


class ClusterInfo(APIModel):
    connection_fingerprint: str | None = None
    identity_verified: bool = False


class DiscoveryResult(APIModel):
    status: Literal["ok", "empty", "partial"]
    cluster: ClusterInfo
    namespace: str
    captured_at: str
    declared_state: Literal["available", "unavailable"] = "unavailable"
    warnings: list[DiscoveryWarning] = []
    unresolved: list[UnresolvedItem] = []
    service_evidence: dict[str, ServiceEvidence] = {}
    graph: DeploymentGraph


def _classify(error: ClusterAccessError) -> DiscoveryError:
    if isinstance(error, NotConnectedError):
        return DiscoveryError("CLUSTER_NOT_CONNECTED", "No Kubernetes cluster is connected.", http_status=503)
    if isinstance(error, ConnectionFailedError):
        return DiscoveryError("CLUSTER_CONNECTION_FAILED",
                              "Could not connect to the selected Kubernetes cluster.",
                              http_status=502, retryable=True)
    if isinstance(error, AuthFailedError):
        return DiscoveryError("CLUSTER_AUTH_FAILED",
                              "The cluster rejected the stored credential. It may have expired or been revoked.",
                              http_status=502)
    if isinstance(error, ForbiddenError):
        return DiscoveryError("CLUSTER_FORBIDDEN",
                              f"The credential is not allowed to {error.verb} {error.resource}.",
                              http_status=502)
    return DiscoveryError("DISCOVERY_FAILED", "The cluster returned an unexpected error during discovery.",
                          http_status=502, retryable=True)


def _unresolved(kind: str, name: str, reason: str, namespace: str, affects: list[str] | None = None) -> UnresolvedItem:
    return UnresolvedItem(kind=kind, name=name, reason=reason, namespace=namespace, affects=affects or [])


def discover(namespace: str, source: ClusterSource | None = None) -> DiscoveryResult:
    namespace = (namespace or "").strip()
    if not NAMESPACE_PATTERN.match(namespace):
        raise DiscoveryError("INVALID_NAMESPACE", "The namespace is not a valid Kubernetes namespace name.",
                             http_status=422)

    source = source or get_cluster_source()
    try:
        nodes = source.list_nodes()
        pods = source.list_pods(namespace)
        deployments = source.list_deployments(namespace)
        fingerprint = source.connection_fingerprint()
    except ClusterAccessError as error:
        logger.warning("Cluster discovery failed (%s): %s", type(error).__name__, error)
        raise _classify(error) from None
    except DiscoveryError:
        raise
    except Exception:
        # The exception text may contain request details; keep it in the server log only.
        logger.exception("Unexpected failure during cluster discovery")
        raise DiscoveryError("DISCOVERY_FAILED", "The cluster returned an unexpected error during discovery.",
                             http_status=502, retryable=True) from None

    workers = [n for n in nodes if not n.control_plane]
    worker_names = {n.name for n in workers}
    warnings: list[DiscoveryWarning] = []
    unresolved: list[UnresolvedItem] = []
    evidence_reasons: dict[str, list[str]] = {}

    if not workers:
        warnings.append(DiscoveryWarning(
            code="NO_WORKER_NODES",
            message="The cluster reports no worker nodes, so no failure domains can be modelled.",
        ))

    workloads = resolve_deployment_ownership(pods, deployments)

    for name, workload in workloads.items():
        reasons = evidence_reasons.setdefault(name, [])
        for pod_name in workload["unscheduledPods"]:
            unresolved.append(_unresolved("Pod", pod_name, "POD_NOT_SCHEDULED", namespace, [name]))
            reasons.append("UNSCHEDULED_PODS")
        placed, excluded = [], []
        for pod in workload["pods"]:
            (placed if pod["node"] in worker_names else excluded).append(pod)
        for pod in excluded:
            unresolved.append(_unresolved("Pod", pod["name"], "POD_ON_EXCLUDED_NODE", namespace, [name]))
            reasons.append("PODS_ON_EXCLUDED_NODES")
        workload["pods"] = placed
        workload["readyReplicas"] = sum(1 for p in placed if p["ready"])
        observed = len(placed) + len(excluded) + len(workload["unscheduledPods"])
        if workload["declaredReplicas"] > 0 and observed == 0:
            unresolved.append(_unresolved("Deployment", name, "WORKLOAD_HAS_NO_PODS", namespace, [name]))
            reasons.append("NO_PODS_OBSERVED")
        if workload["declaredReplicas"] == 0:
            warnings.append(DiscoveryWarning(
                code="WORKLOAD_SCALED_TO_ZERO",
                message=f"Deployment '{name}' is scaled to zero replicas.",
            ))

    for pod in find_unattributed_pods(pods, deployments):
        related = related_deployments(pod, deployments)
        unresolved.append(_unresolved("Pod", pod.name, "NO_MATCHING_DEPLOYMENT", namespace, related))
        for name in related:
            evidence_reasons.setdefault(name, []).append("UNMATCHED_RELATED_PODS")

    service_evidence = {
        name: ServiceEvidence(complete=not reasons, reasons=sorted(set(reasons)))
        for name, reasons in evidence_reasons.items()
    }

    if not pods and not deployments:
        warnings.append(DiscoveryWarning(
            code="NAMESPACE_EXISTENCE_UNVERIFIED",
            message=f"No workloads were found in '{namespace}'. Whether the namespace exists could not be "
                    "verified with the current read permissions.",
        ))

    builder = DeploymentGraphBuilder()
    builder.attach_nodes(workers)
    builder.attach_services(analyze_replica_concentration(workloads), {})
    graph = builder.build()

    if unresolved or not workers:
        status = "partial"
    elif not graph.services:
        status = "empty"
    else:
        status = "ok"

    return DiscoveryResult(
        status=status,
        cluster=ClusterInfo(connection_fingerprint=fingerprint, identity_verified=False),
        namespace=namespace,
        captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        warnings=warnings,
        unresolved=unresolved,
        service_evidence=service_evidence,
        graph=graph,
    )
