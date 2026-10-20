"""Read-only Kubernetes access for cluster discovery.

This is integration code, kept apart from the research algorithms in ``app/engine``. It lists nodes,
pods, deployments, services and replicasets and hands back plain dataclasses; it never creates, changes or deletes anything.

Design points:
* Each ``KubernetesApiSource`` reads the kubeconfig it is pointed at (``config_file``, else
  ``$KUBECONFIG``, else ``~/.kube/config``) and talks to the API server through ``rest.py``'s small httpx
  client. It keeps no process-wide state, so switching clusters cannot leave a stale client behind, and
  a refreshed credential is never written back to the kubeconfig file.
* Every request has a timeout.
* Failures are classified into the ``ClusterAccessError`` family. Messages never include request
  headers, kubeconfig content or tokens.
"""

import hashlib
import os
from urllib.parse import quote

import httpx

from app.integrations.kubernetes import rest
from app.integrations.kubernetes.models import (
    ClusterDeployment,
    ClusterNode,
    ClusterPod,
    ClusterReplicaSet,
    ClusterService,
)

DEFAULT_REQUEST_TIMEOUT_SECONDS = 8
CONTROL_PLANE_LABELS = ("node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master")
ZONE_LABEL = "topology.kubernetes.io/zone"


class ClusterAccessError(Exception):
    """Base class for every way reading the cluster can fail."""


class NotConnectedError(ClusterAccessError):
    """No usable kubeconfig is available."""


class ConnectionFailedError(ClusterAccessError):
    """The API server could not be reached (network, DNS, TLS or timeout)."""


class AuthFailedError(ClusterAccessError):
    """The API server rejected the credential (HTTP 401)."""


class ForbiddenError(ClusterAccessError):
    """The credential may not perform a required read (HTTP 403)."""

    def __init__(self, verb: str, resource: str) -> None:
        super().__init__(f"{verb} {resource} is forbidden")
        self.verb = verb
        self.resource = resource


class ApiFailedError(ClusterAccessError):
    """The API server answered with another error."""

    def __init__(self, status: int | None) -> None:
        super().__init__(f"the API server returned HTTP {status}")
        self.status = status


def _meta(item: dict) -> dict:
    return item.get("metadata") or {}


def normalize_node(node: dict) -> ClusterNode:
    labels = _meta(node).get("labels") or {}
    allocatable = (node.get("status") or {}).get("allocatable") or {}
    return ClusterNode(
        name=_meta(node).get("name"),
        zone=labels.get(ZONE_LABEL),
        cpu_available=allocatable.get("cpu"),
        memory_available=allocatable.get("memory"),
        control_plane=any(label in labels for label in CONTROL_PLANE_LABELS),
    )


def normalize_pod(pod: dict) -> ClusterPod:
    meta, status = _meta(pod), pod.get("status") or {}
    terminating = meta.get("deletionTimestamp") is not None
    ready_condition = any(
        c.get("type") == "Ready" and c.get("status") == "True" for c in (status.get("conditions") or [])
    )
    owners = meta.get("ownerReferences") or []
    statuses = status.get("containerStatuses")
    labels = meta.get("labels") or {}
    return ClusterPod(
        name=meta.get("name"),
        node=(pod.get("spec") or {}).get("nodeName") or None,
        ready=ready_condition and not terminating,
        owner_kind=owners[0].get("kind") if owners else None,
        owner_name=owners[0].get("name") if owners else None,
        app_label=labels.get("app", ""),
        labels=dict(labels),
        phase=status.get("phase"),
        terminating=terminating,
        restart_count=None if statuses is None else sum(s.get("restartCount") or 0 for s in statuses),
    )


def normalize_deployment(deployment: dict) -> ClusterDeployment:
    replicas = (deployment.get("spec") or {}).get("replicas")
    return ClusterDeployment(
        name=_meta(deployment).get("name"),
        declared_replicas=1 if replicas is None else replicas,  # Kubernetes' own default
    )


def normalize_service(service: dict) -> ClusterService:
    return ClusterService(
        name=_meta(service).get("name"), selector=dict((service.get("spec") or {}).get("selector") or {})
    )


def normalize_replicaset(replicaset: dict) -> ClusterReplicaSet:
    owners = _meta(replicaset).get("ownerReferences") or []
    deployment = next((o.get("name") for o in owners if o.get("kind") == "Deployment"), None)
    return ClusterReplicaSet(name=_meta(replicaset).get("name"), deployment_name=deployment)


class KubernetesApiSource:
    def __init__(
        self,
        config_file: str | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self._config_file = config_file
        self._timeout = request_timeout
        self._transport = transport  # a seam for tests; production always uses the network

    # -- connection -------------------------------------------------------------------------------

    def _paths(self) -> list[str]:
        raw = self._config_file or os.environ.get("KUBECONFIG") or "~/.kube/config"
        paths = [os.path.expanduser(p) for p in raw.split(os.pathsep) if p]
        return [p for p in paths if os.path.isfile(p)]

    def _open(self) -> rest.RestClient:
        paths = self._paths()
        if not paths:
            raise NotConnectedError("no kubeconfig is available")
        try:
            return rest.RestClient(rest.load_connection(paths), self._timeout, transport=self._transport)
        except rest.KubeconfigError as error:
            # The message names the problem (for example "a credential plugin is not supported"); it never
            # carries kubeconfig content, a path or a credential.
            raise NotConnectedError(f"the kubeconfig is not usable: {error}") from error

    def _list(self, resource: str, path: str) -> list[dict]:
        http = self._open()
        try:
            body = http.get_json(path)
        except rest.HttpStatusError as error:
            if error.status == 401:
                raise AuthFailedError("the cluster rejected the credential") from None
            if error.status == 403:
                raise ForbiddenError("list", resource) from None
            raise ApiFailedError(error.status) from None
        except rest.TransportFailure as error:
            raise ConnectionFailedError(error.name) from None
        finally:
            http.close()
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise ApiFailedError(None)
        return items

    # -- reads ------------------------------------------------------------------------------------

    def list_nodes(self) -> list[ClusterNode]:
        return [normalize_node(n) for n in self._list("nodes", "/api/v1/nodes")]

    def list_pods(self, namespace: str) -> list[ClusterPod]:
        items = self._list("pods", f"/api/v1/namespaces/{quote(namespace, safe='')}/pods")
        return [normalize_pod(p) for p in items]

    def list_deployments(self, namespace: str) -> list[ClusterDeployment]:
        items = self._list("deployments", f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments")
        return [normalize_deployment(d) for d in items]

    def list_services(self, namespace: str) -> list[ClusterService]:
        items = self._list("services", f"/api/v1/namespaces/{quote(namespace, safe='')}/services")
        return [normalize_service(s) for s in items]

    def list_replicasets(self, namespace: str) -> list[ClusterReplicaSet]:
        items = self._list("replicasets", f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/replicasets")
        return [normalize_replicaset(r) for r in items]

    def list_workload_namespaces(self) -> list[str]:
        """Sorted names of the namespaces that hold at least one pod or deployment, cluster-wide.

        The extension's ClusterRole is cluster-scoped, so this needs no permission beyond the pod and
        deployment reads it already has. Namespaces with no workloads, and namespaces that merely exist,
        are not listed: Namespace objects are deliberately not readable.
        """
        pods = self._list("pods", "/api/v1/pods")
        deployments = self._list("deployments", "/apis/apps/v1/deployments")
        namespaces = {_meta(item).get("namespace") for item in [*pods, *deployments]}
        return sorted(namespace for namespace in namespaces if namespace)

    def connection_fingerprint(self) -> str | None:
        """A hash of the API server address and CA, never of a credential.

        It changes when the connection points at a different cluster and stays the same when only a
        token is renewed. It is used to invalidate state, not to prove which cluster this is.
        """
        paths = self._paths()
        if not paths:
            return None
        identity = rest.cluster_identity(paths)
        if identity is None:
            return None
        server, ca = identity
        return hashlib.sha256(f"{server}\0{ca}".encode("utf-8")).hexdigest()[:32]
