"""Live cluster discovery for Component 2 (Deployment Topology Modeling).

``discover()`` reads the connected cluster through a ``ClusterSource`` and returns a ``DiscoveryResult``:
the unchanged ``DeploymentGraph`` contract wrapped in an envelope that says how much of the cluster was
actually resolved. It either returns real evidence or raises ``DiscoveryError``; there is no fallback to
sample data, and nothing is invented to fill a gap.

How pods are attributed, and the limits that remain, stated rather than hidden:
* a Service owns the pods its label selector matches, and each pod's Deployment comes from its owner
  references (pod -> ReplicaSet -> Deployment); names are never compared. Pods that no Service selects are
  named in a warning and are not part of any service graph;
* the namespace's existence cannot be verified (no Namespace read permission);
* the connection fingerprint detects a change of connection, it does not prove which cluster this is.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Literal, Protocol
from urllib.parse import quote

from app.engine.deployment_topology_modeling.graph_builder import DeploymentGraphBuilder
from app.engine.deployment_topology_modeling.live_state.cluster_models import (
    ClusterDeployment,
    ClusterNode,
    ClusterPod,
)
from app.engine.deployment_topology_modeling.live_state.concentration_analyzer import (
    analyze_replica_concentration,
)
from app.engine.deployment_topology_modeling.live_state.cluster_state_parser import TERMINAL_POD_PHASES
from app.engine.deployment_topology_modeling.live_state.ownership_resolver import resolve_service_ownership
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
    # Raw API objects of Services, ReplicaSets and Deployments, which the Service resolver reads.
    def list_objects(self, resource: str, path: str) -> list[dict]: ...
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


# Public name for callers outside this module (service.py) that read the cluster another way but must
# report failures with the same stable codes.
classify_access_error = _classify


def _unresolved(kind: str, name: str, reason: str, namespace: str, affects: list[str] | None = None) -> UnresolvedItem:
    return UnresolvedItem(kind=kind, name=name, reason=reason, namespace=namespace, affects=affects or [])


def _read_cluster(source: ClusterSource, namespace: str) -> tuple:
    """The five read-only lists the whole model is built from, plus the connection fingerprint."""
    try:
        nodes = source.list_nodes()
        pods = source.list_pods(namespace)
        base = quote(namespace, safe="")
        services = source.list_objects("services", f"/api/v1/namespaces/{base}/services")
        replica_sets = source.list_objects("replicasets", f"/apis/apps/v1/namespaces/{base}/replicasets")
        deployments = source.list_objects("deployments", f"/apis/apps/v1/namespaces/{base}/deployments")
        fingerprint = source.connection_fingerprint()
    except ClusterAccessError as error:
        # No connection, bad credential, missing permission...: answer with a clear error code, never sample data.
        logger.warning("Cluster discovery failed (%s): %s", type(error).__name__, error)
        raise _classify(error) from None
    except DiscoveryError:
        raise
    except Exception:
        # The exception text may contain request details; keep it in the server log only.
        logger.exception("Unexpected failure during cluster discovery")
        raise DiscoveryError("DISCOVERY_FAILED", "The cluster returned an unexpected error during discovery.",
                             http_status=502, retryable=True) from None
    return nodes, pods, services, replica_sets, deployments, fingerprint


def _check_service(
    name: str, workload: dict, namespace: str, worker_names: set[str],
) -> tuple[list[UnresolvedItem], list[str], list[DiscoveryWarning]]:
    """Keeps only the Service's pods that run on a worker, and returns what could not be resolved about it.

    Sorts the pods: on a worker (placed), not scheduled yet, or on a node we don't model (excluded).
    Anything not placed is reported as unresolved instead of being guessed.
    """
    unresolved: list[UnresolvedItem] = []
    reasons: list[str] = []
    warnings: list[DiscoveryWarning] = []
    all_pods = workload["pods"]
    placed, excluded = [], []
    for pod in all_pods:
        if pod["node"] is None:
            unresolved.append(_unresolved("Pod", pod["name"], "POD_NOT_SCHEDULED", namespace, [name]))
            reasons.append("UNSCHEDULED_PODS")
        elif pod["node"] in worker_names:
            placed.append(pod)
        else:
            excluded.append(pod)
    for pod in excluded:
        unresolved.append(_unresolved("Pod", pod["name"], "POD_ON_EXCLUDED_NODE", namespace, [name]))
        reasons.append("PODS_ON_EXCLUDED_NODES")
    for pod_name in workload["podsWithoutDeploymentOwner"]:
        # Selected by the Service but owned by a StatefulSet, a DaemonSet or nothing: the pod serves the
        # Service's traffic, but its declared replica count is not known from a Deployment.
        unresolved.append(_unresolved("Pod", pod_name, "POD_WITHOUT_DEPLOYMENT_OWNER", namespace, [name]))
        reasons.append("PODS_WITHOUT_DEPLOYMENT_OWNER")
    workload["pods"] = placed
    workload["readyReplicas"] = sum(1 for p in placed if p["ready"])
    if workload["declaredReplicas"] > 0 and not all_pods:
        unresolved.append(_unresolved("Service", name, "WORKLOAD_HAS_NO_PODS", namespace, [name]))
        reasons.append("NO_PODS_OBSERVED")
    if workload["deploymentNames"] and workload["declaredReplicas"] == 0:
        warnings.append(DiscoveryWarning(
            code="WORKLOAD_SCALED_TO_ZERO",
            message=f"The workload behind Service '{name}' is scaled to zero replicas.",
        ))
    return unresolved, reasons, warnings


def _stray_pod_warning(pods: list[ClusterPod], selected_pods: set[str], namespace: str) -> DiscoveryWarning | None:
    """Pods that no Service selects (a Job's pod, a standalone pod) are not part of any service graph, so they
    cannot change the placement of one. They are named in a warning but do not make the result partial."""
    stray = sorted(p.name for p in pods if p.name not in selected_pods and p.phase not in TERMINAL_POD_PHASES)
    if not stray:
        return None
    shown = ", ".join(stray[:5]) + (f" and {len(stray) - 5} more" if len(stray) > 5 else "")
    return DiscoveryWarning(
        code="POD_SELECTED_BY_NO_SERVICE",
        message=f"{len(stray)} pod(s) in '{namespace}' are selected by no Service and are not part of any "
                f"service graph: {shown}.",
    )


def _status(unresolved: list[UnresolvedItem], workers: list[ClusterNode], graph: DeploymentGraph) -> str:
    """partial = something could not be resolved; empty = no services; ok = everything resolved."""
    if unresolved or not workers:
        return "partial"
    if not graph.services:
        return "empty"
    return "ok"


def discover(namespace: str, source: ClusterSource | None = None) -> DiscoveryResult:
    namespace = (namespace or "").strip()
    # Reject names Kubernetes would never allow before anything is sent to the cluster.
    if not NAMESPACE_PATTERN.match(namespace):
        raise DiscoveryError("INVALID_NAMESPACE", "The namespace is not a valid Kubernetes namespace name.",
                             http_status=422)

    nodes, pods, services, replica_sets, deployments, fingerprint = _read_cluster(
        source or get_cluster_source(), namespace)

    # Worker nodes are the failure domains: application pods only run there.
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

    # Which pods belong to which Service is decided the way Kubernetes decides it: a Service owns the pods
    # whose labels contain every pair of its selector, and each pod's Deployment comes from its owner
    # references (pod -> ReplicaSet -> Deployment). Names are never compared, so an application whose
    # Deployments are named differently from their ``app`` label (Istio Bookinfo's ``productpage-v1``)
    # resolves completely instead of being reported as unmatched.
    resolved = resolve_service_ownership(pods, services, replica_sets, deployments, namespace)
    selected_pods: set[str] = set()
    workloads: dict[str, dict] = {}

    for name, workload in resolved.items():
        selected_pods.update(pod["name"] for pod in workload["pods"])
        if not workload["pods"] and not workload["deploymentNames"]:
            # A Service that selects nothing (for example one that fronts something outside the cluster)
            # has no placement to model. It is said so, not silently dropped or guessed at.
            warnings.append(DiscoveryWarning(
                code="SERVICE_SELECTS_NO_PODS",
                message=f"Service '{name}' selects no pods in '{namespace}', so it has no placement to model.",
            ))
            continue
        service_unresolved, reasons, service_warnings = _check_service(name, workload, namespace, worker_names)
        unresolved += service_unresolved
        warnings += service_warnings
        evidence_reasons[name] = reasons
        workloads[name] = workload

    stray_warning = _stray_pod_warning(pods, selected_pods, namespace)
    if stray_warning:
        warnings.append(stray_warning)

    service_evidence = {
        name: ServiceEvidence(complete=not reasons, reasons=sorted(set(reasons)))
        for name, reasons in evidence_reasons.items()
        if name in workloads
    }

    if not pods and not deployments:
        warnings.append(DiscoveryWarning(
            code="NAMESPACE_EXISTENCE_UNVERIFIED",
            message=f"No workloads were found in '{namespace}'. Whether the namespace exists could not be "
                    "verified with the current read permissions.",
        ))

    # Concentration and exposure per service, then the shared DeploymentGraph.
    builder = DeploymentGraphBuilder()
    builder.attach_nodes(workers)
    builder.attach_services(analyze_replica_concentration(workloads), {})
    graph = builder.build()

    return DiscoveryResult(
        status=_status(unresolved, workers, graph),
        cluster=ClusterInfo(connection_fingerprint=fingerprint, identity_verified=False),
        namespace=namespace,
        captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        warnings=warnings,
        unresolved=unresolved,
        service_evidence=service_evidence,
        graph=graph,
    )
