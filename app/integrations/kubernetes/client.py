"""Read-only Kubernetes access for cluster discovery.

This is integration code, kept apart from the research algorithms in ``app/engine``. It lists nodes,
pods, deployments, services and replicasets and hands back plain dataclasses; it never creates, changes or deletes anything.

Design points:
* Each ``KubernetesApiSource`` builds its own ``ApiClient`` from the kubeconfig it is pointed at
  (``config_file``, else ``$KUBECONFIG``, else ``~/.kube/config``) and never mutates the process-wide
  default Configuration, so switching clusters cannot leave a stale client behind.
* ``persist_config=False``: a refreshed credential is never written back to the kubeconfig file.
* Every request has a timeout.
* Failures are classified into the ``ClusterAccessError`` family. Messages never include request
  headers, kubeconfig content or tokens.
"""

import hashlib
import os
import urllib3
import yaml
from typing import Any, Callable

from kubernetes import client, config
from kubernetes.client.rest import ApiException

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


def _plain(node: Any) -> Any:
    """kubernetes' kubeconfig loader wraps entries in ConfigNode objects; unwrap to plain data."""
    return node.value if hasattr(node, "value") else node


def normalize_node(node: Any) -> ClusterNode:
    labels = node.metadata.labels or {}
    allocatable = node.status.allocatable or {}
    return ClusterNode(
        name=node.metadata.name,
        zone=labels.get(ZONE_LABEL),
        cpu_available=allocatable.get("cpu"),
        memory_available=allocatable.get("memory"),
        control_plane=any(label in labels for label in CONTROL_PLANE_LABELS),
    )


def normalize_pod(pod: Any) -> ClusterPod:
    terminating = pod.metadata.deletion_timestamp is not None
    ready_condition = any(
        c.type == "Ready" and c.status == "True" for c in (pod.status.conditions or [])
    )
    owners = pod.metadata.owner_references or []
    statuses = pod.status.container_statuses
    labels = pod.metadata.labels or {}
    return ClusterPod(
        name=pod.metadata.name,
        node=pod.spec.node_name or None,
        ready=ready_condition and not terminating,
        owner_kind=owners[0].kind if owners else None,
        owner_name=owners[0].name if owners else None,
        app_label=labels.get("app", ""),
        labels=dict(labels),
        phase=pod.status.phase,
        terminating=terminating,
        restart_count=None if statuses is None else sum(s.restart_count or 0 for s in statuses),
    )


def normalize_deployment(deployment: Any) -> ClusterDeployment:
    replicas = deployment.spec.replicas
    return ClusterDeployment(
        name=deployment.metadata.name,
        declared_replicas=1 if replicas is None else replicas,  # Kubernetes' own default
    )


def normalize_service(service: Any) -> ClusterService:
    return ClusterService(name=service.metadata.name, selector=dict(service.spec.selector or {}))


def normalize_replicaset(replicaset: Any) -> ClusterReplicaSet:
    owners = replicaset.metadata.owner_references or []
    deployment = next((o.name for o in owners if o.kind == "Deployment"), None)
    return ClusterReplicaSet(name=replicaset.metadata.name, deployment_name=deployment)


class KubernetesApiSource:
    def __init__(self, config_file: str | None = None, request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS):
        self._config_file = config_file
        self._timeout = request_timeout

    # -- connection -------------------------------------------------------------------------------

    def _paths(self) -> list[str]:
        raw = self._config_file or os.environ.get("KUBECONFIG") or "~/.kube/config"
        paths = [os.path.expanduser(p) for p in raw.split(os.pathsep) if p]
        return [p for p in paths if os.path.isfile(p)]

    def _api_client(self) -> client.ApiClient:
        paths = self._paths()
        if not paths:
            raise NotConnectedError("no kubeconfig is available")
        try:
            return config.new_client_from_config(
                config_file=os.pathsep.join(paths), persist_config=False
            )
        except (config.ConfigException, yaml.YAMLError, OSError, ValueError, KeyError, TypeError) as error:
            raise NotConnectedError("the kubeconfig is not usable") from error

    def _list(self, resource: str, call: Callable[[client.ApiClient], Any]) -> list[Any]:
        api_client = self._api_client()
        try:
            return list(call(api_client).items)
        except ApiException as error:
            if error.status == 401:
                raise AuthFailedError("the cluster rejected the credential") from None
            if error.status == 403:
                raise ForbiddenError("list", resource) from None
            raise ApiFailedError(error.status) from None
        except (urllib3.exceptions.HTTPError, OSError) as error:
            raise ConnectionFailedError(type(error).__name__) from None
        finally:
            api_client.close()

    # -- reads ------------------------------------------------------------------------------------

    def list_nodes(self) -> list[ClusterNode]:
        items = self._list("nodes", lambda c: client.CoreV1Api(c).list_node(_request_timeout=self._timeout))
        return [normalize_node(n) for n in items]

    def list_pods(self, namespace: str) -> list[ClusterPod]:
        items = self._list(
            "pods",
            lambda c: client.CoreV1Api(c).list_namespaced_pod(namespace, _request_timeout=self._timeout),
        )
        return [normalize_pod(p) for p in items]

    def list_deployments(self, namespace: str) -> list[ClusterDeployment]:
        items = self._list(
            "deployments",
            lambda c: client.AppsV1Api(c).list_namespaced_deployment(namespace, _request_timeout=self._timeout),
        )
        return [normalize_deployment(d) for d in items]

    def list_services(self, namespace: str) -> list[ClusterService]:
        items = self._list(
            "services",
            lambda c: client.CoreV1Api(c).list_namespaced_service(namespace, _request_timeout=self._timeout),
        )
        return [normalize_service(s) for s in items]

    def list_replicasets(self, namespace: str) -> list[ClusterReplicaSet]:
        items = self._list(
            "replicasets",
            lambda c: client.AppsV1Api(c).list_namespaced_replica_set(namespace, _request_timeout=self._timeout),
        )
        return [normalize_replicaset(r) for r in items]

    def connection_fingerprint(self) -> str | None:
        """A hash of the API server address and CA, never of a credential.

        It changes when the connection points at a different cluster and stays the same when only a
        token is renewed. It is used to invalidate state, not to prove which cluster this is.
        """
        paths = self._paths()
        if not paths:
            return None
        try:
            document = config.kube_config.KubeConfigMerger(os.pathsep.join(paths)).config.value
            contexts = {c["name"]: c.get("context") or {} for c in map(_plain, document.get("contexts") or [])}
            chosen = contexts.get(document.get("current-context"))
            clusters = {c["name"]: c.get("cluster") or {} for c in map(_plain, document.get("clusters") or [])}
            cluster = clusters.get(chosen.get("cluster")) if chosen else None
            if not cluster or not cluster.get("server"):
                return None
            ca = cluster.get("certificate-authority-data") or cluster.get("certificate-authority") or ""
            return hashlib.sha256(f"{cluster['server']}\0{ca}".encode("utf-8")).hexdigest()[:32]
        except (config.ConfigException, yaml.YAMLError, OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
