"""Contact with ONE designated test cluster, for a validation run. Reads by default; writes only when explicitly enabled.

This is the real counterpart of the in-memory gateway the validation tests use. It satisfies the engine's ``ClusterGateway``
protocol by shape and imports nothing from ``app.engine``: the objects it returns carry the same attributes as the
engine's ``ClusterIdentity`` and ``LiveDeployment``, and the topology comes from Member 2's discovery, which the caller
passes in as ``topology_reader`` (so the algorithm that builds a ``DeploymentGraph`` is Member 2's, unchanged).

Which cluster. The constructor takes the path of a credentials file that was named for this purpose and nothing else. It
refuses the ambient ones: ``~/.kube/config`` and every file in ``$KUBECONFIG``. That is not pedantry: ``kind create cluster``
rewrites the default file's current context unless told otherwise, which would silently repoint the backend and the
installed extension at the new cluster. The gateway does not decide that a cluster is the right one; the executor compares
``identity()`` with a person's designation before anything else happens.

Reads: the identity (UID of the ``kube-system`` namespace, API address, context, node names), the topology, the Deployments,
rollout and node readiness, and a capacity report. The shared ``RestClient`` stays GET/POST only; the one place a PATCH can
be sent is ``_WriteClient`` below, and a PATCH is sent only when the gateway was built with ``allow_writes=True``.

Nothing here has ever been run against a real cluster. Its tests use a fake API server over ``httpx.MockTransport``.
"""

import json
import os
import ssl
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.kubernetes import rest
from app.integrations.kubernetes.client import (
    ApiFailedError,
    AuthFailedError,
    ConnectionFailedError,
    ForbiddenError,
    KubernetesApiSource,
    NotConnectedError,
)
from app.integrations.kubernetes.quantity import parse_quantity

REAL_CLUSTER = "REAL_CLUSTER"
HOSTNAME = "kubernetes.io/hostname"
CONTROL_PLANE_LABELS = ("node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master")
STRATEGIC_MERGE = "application/strategic-merge-patch+json"
REQUIRED_ANTI_AFFINITY = "requiredDuringSchedulingIgnoredDuringExecution"
PREFERRED_ANTI_AFFINITY = "preferredDuringSchedulingIgnoredDuringExecution"


class WritesDisabledError(RuntimeError):
    """A write was asked of a gateway that was not built to write."""


class AmbientCredentialsError(ValueError):
    """The credentials file is one the rest of the system uses; a validation gateway never borrows those."""


class ConflictError(ApiFailedError):
    """The object changed since it was read (HTTP 409)."""


@dataclass(frozen=True)
class ObservedIdentity:
    """Same attributes as the engine's ``ClusterIdentity``."""

    context: str | None
    api_server: str
    cluster_uid: str | None
    node_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservedDeployment:
    """Same attributes as the engine's ``LiveDeployment``."""

    name: str
    namespace: str
    resource_version: str
    generation: int
    replicas: int
    selector: Mapping[str, str]
    pod_labels: Mapping[str, str]
    topology_spread_constraints: tuple[dict[str, Any], ...] = ()
    pod_anti_affinity: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_hostname_spread(self) -> bool:
        return any(c.get("topologyKey") == HOSTNAME for c in self.topology_spread_constraints)

    @property
    def has_required_anti_affinity(self) -> bool:
        return bool((self.pod_anti_affinity or {}).get(REQUIRED_ANTI_AFFINITY))


@dataclass(frozen=True)
class NodeCapacity:
    """What a node can still take, from allocatable minus the requests of every pod on it (system pods included)."""

    name: str
    control_plane: bool
    ready: bool | None
    schedulable: bool
    allocatable_cpu: float | None
    allocatable_memory: float | None
    requested_cpu: float
    requested_memory: float
    pods: int
    unreadable_requests: int          # pods whose requests could not be read; the free figures are upper bounds if > 0

    @property
    def free_cpu(self) -> float | None:
        return None if self.allocatable_cpu is None else self.allocatable_cpu - self.requested_cpu

    @property
    def free_memory(self) -> float | None:
        return None if self.allocatable_memory is None else self.allocatable_memory - self.requested_memory


def _refuse_ambient(path: str) -> str:
    if not path or not str(path).strip():
        raise AmbientCredentialsError("a validation gateway needs the path of its own credentials file")
    real = os.path.realpath(os.path.expanduser(str(path)))
    ambient = {os.path.realpath(os.path.expanduser("~/.kube/config"))}
    ambient.update(os.path.realpath(os.path.expanduser(p)) for p in os.environ.get("KUBECONFIG", "").split(os.pathsep) if p)
    if real in ambient:
        raise AmbientCredentialsError("that credentials file is the one the rest of the system uses (the default kubeconfig or "
                                      "one named in $KUBECONFIG); a validation gateway takes a file made for it alone")
    if not os.path.isfile(real):
        raise AmbientCredentialsError("the credentials file does not exist")
    return real


class _WriteClient(rest.RestClient):
    """The only client that can PATCH. The product's ``RestClient`` stays read-only."""

    def patch_json(self, path: str, body: dict) -> Any:
        extensions = {"sni_hostname": self._server_name} if self._server_name else None
        try:
            response = self._http.request("PATCH", path, content=json.dumps(body), extensions=extensions,
                                          headers={"Content-Type": STRATEGIC_MERGE})
        except (httpx.TransportError, ssl.SSLError, OSError) as error:
            raise rest.TransportFailure(type(error).__name__) from None
        if not 200 <= response.status_code < 300:
            raise rest.HttpStatusError(response.status_code)
        try:
            return response.json()
        except ValueError:
            raise rest.TransportFailure("InvalidResponse") from None


def _meta(item: dict) -> dict:
    return item.get("metadata") or {}


def _ready_condition(node: dict) -> bool | None:
    """True/False from the node's Ready condition; Unknown (the control plane lost contact) counts as not ready."""
    for condition in (node.get("status") or {}).get("conditions") or []:
        if condition.get("type") == "Ready":
            return condition.get("status") == "True"
    return None


class KubernetesTestClusterGateway:
    @property
    def evidence_class(self) -> str:
        """REAL_CLUSTER only when talking over the network. A gateway given a test transport is a test double, whatever its class."""
        return REAL_CLUSTER if self._transport is None else "TEST_DOUBLE"

    def __init__(self, kubeconfig_path: str, *, topology_reader: Callable[[str, Any], Any], allow_writes: bool = False,
                 timeout: float = 10.0, transport: httpx.BaseTransport | None = None) -> None:
        self._path = _refuse_ambient(kubeconfig_path)
        self._reader, self._timeout, self._transport = topology_reader, timeout, transport
        self.allow_writes = allow_writes
        self._source = KubernetesApiSource(config_file=self._path, request_timeout=timeout, transport=transport)
        self.last_discovery: Any = None

    # -- plumbing ----------------------------------------------------------------------------------------------------

    def _client(self, client_class: type[rest.RestClient] = rest.RestClient) -> rest.RestClient:
        try:
            return client_class(rest.load_connection([self._path]), self._timeout, transport=self._transport)
        except rest.KubeconfigError as error:
            raise NotConnectedError(f"the credentials file is not usable: {error}") from error

    def _get(self, path: str, what: str) -> Any:
        http = self._client()
        try:
            return http.get_json(path)
        except rest.HttpStatusError as error:
            if error.status == 401:
                raise AuthFailedError("the cluster rejected the credential") from None
            if error.status == 403:
                raise ForbiddenError("get", what) from None
            raise ApiFailedError(error.status) from None
        except rest.TransportFailure as error:
            raise ConnectionFailedError(error.name) from None
        finally:
            http.close()

    def _items(self, resource: str, path: str) -> list[dict]:
        return self._source.list_objects(resource, path)

    # -- reads -------------------------------------------------------------------------------------------------------

    def identity(self) -> ObservedIdentity:
        connection = rest.load_connection([self._path])
        context = rest.read_kubeconfig_file(self._path).get("current-context")
        try:
            uid = _meta(self._get("/api/v1/namespaces/kube-system", "namespaces")).get("uid")
        except (ForbiddenError, ApiFailedError):
            uid = None      # unreadable: reported as unverified, never guessed
        names = tuple(_meta(n).get("name") for n in self._items("nodes", "/api/v1/nodes"))
        return ObservedIdentity(context, connection.server, uid, names)

    def read_graph(self, namespace: str):
        """Member 2's ``DeploymentGraph`` for the namespace, with each node's Ready and schedulable state read from the nodes."""
        result = self._reader(namespace, self._source)
        self.last_discovery = result
        graph = result.graph
        raw = {_meta(n).get("name"): n for n in self._items("nodes", "/api/v1/nodes")}
        for node in graph.nodes:
            if node.name in raw:
                node.ready = _ready_condition(raw[node.name])
                node.schedulable = not (raw[node.name].get("spec") or {}).get("unschedulable", False)
        return graph

    def read_deployments(self, namespace: str) -> dict[str, ObservedDeployment]:
        items = self._items("deployments", f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments")
        found = {}
        for item in items:
            meta, spec = _meta(item), item.get("spec") or {}
            template = (spec.get("template") or {})
            pod_spec = template.get("spec") or {}
            replicas = spec.get("replicas")
            found[meta["name"]] = ObservedDeployment(
                name=meta["name"], namespace=meta.get("namespace") or namespace,
                resource_version=str(meta.get("resourceVersion") or ""), generation=int(meta.get("generation") or 0),
                replicas=1 if replicas is None else int(replicas),      # Kubernetes' own default
                selector=dict((spec.get("selector") or {}).get("matchLabels") or {}),
                pod_labels=dict(_meta(template).get("labels") or {}),
                topology_spread_constraints=tuple(pod_spec.get("topologySpreadConstraints") or ()),
                pod_anti_affinity=deepcopy((pod_spec.get("affinity") or {}).get("podAntiAffinity")) or None)
        return found

    def rollout_complete(self, namespace: str, names: Sequence[str]) -> bool:
        items = {_meta(i).get("name"): i for i in self._items(
            "deployments", f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments")}
        for name in names:
            item = items.get(name)
            if item is None:
                return False
            meta, spec, status = _meta(item), item.get("spec") or {}, item.get("status") or {}
            wanted = 1 if spec.get("replicas") is None else spec["replicas"]
            if ((status.get("observedGeneration") or 0) < (meta.get("generation") or 0)
                    or (status.get("updatedReplicas") or 0) != wanted or (status.get("replicas") or 0) != wanted
                    or (status.get("availableReplicas") or 0) != wanted):
                return False
        return True

    def node_ready(self, node: str) -> bool | None:
        return _ready_condition(self._get(f"/api/v1/nodes/{quote(node, safe='')}", "nodes"))

    def read_capacity(self) -> list[NodeCapacity]:
        """Per node: allocatable and requested CPU and memory over every pod in the cluster. Read only."""
        nodes = self._items("nodes", "/api/v1/nodes")
        pods = self._items("pods", "/api/v1/pods")
        requested: dict[str, list[float]] = {}      # node -> [cpu, memory, pods, unreadable]
        for pod in pods:
            node = (pod.get("spec") or {}).get("nodeName")
            if not node or (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
                continue
            totals = requested.setdefault(node, [0.0, 0.0, 0, 0])
            totals[2] += 1
            for container in (pod.get("spec") or {}).get("containers") or []:
                asked = ((container.get("resources") or {}).get("requests")) or {}
                cpu, memory = parse_quantity(asked.get("cpu", 0)), parse_quantity(asked.get("memory", 0))
                if cpu is None or memory is None:
                    totals[3] += 1
                totals[0] += cpu or 0.0
                totals[1] += memory or 0.0
        report = []
        for node in nodes:
            meta = _meta(node)
            allocatable = (node.get("status") or {}).get("allocatable") or {}
            used = requested.get(meta.get("name"), [0.0, 0.0, 0, 0])
            report.append(NodeCapacity(
                name=meta.get("name"), control_plane=any(label in (meta.get("labels") or {}) for label in CONTROL_PLANE_LABELS),
                ready=_ready_condition(node), schedulable=not (node.get("spec") or {}).get("unschedulable", False),
                allocatable_cpu=parse_quantity(allocatable.get("cpu")), allocatable_memory=parse_quantity(allocatable.get("memory")),
                requested_cpu=used[0], requested_memory=used[1], pods=int(used[2]), unreadable_requests=int(used[3])))
        return report

    # -- writes (off unless the gateway was built to write) ------------------------------------------------------------------

    def _require_writes(self) -> None:
        if not self.allow_writes:
            raise WritesDisabledError("this gateway was built read-only; build it with allow_writes=True to let it patch")

    def _patch(self, namespace: str, name: str, body: dict) -> None:
        http = self._client(_WriteClient)
        path = f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments/{quote(name, safe='')}"
        try:
            http.patch_json(path, body)
        except rest.HttpStatusError as error:
            if error.status == 409:
                raise ConflictError(409) from None
            if error.status == 401:
                raise AuthFailedError("the cluster rejected the credential") from None
            if error.status == 403:
                raise ForbiddenError("patch", "deployments") from None
            raise ApiFailedError(error.status) from None
        except rest.TransportFailure as error:
            raise ConnectionFailedError(error.name) from None
        finally:
            http.close()

    def patch_deployment(self, document: dict[str, Any], *, expect_resource_version: str) -> None:
        """Strategic-merge ``document``; the resourceVersion in the body makes the API server refuse it if the object moved."""
        self._require_writes()
        body = deepcopy(document)
        meta = body.setdefault("metadata", {})
        meta["resourceVersion"] = expect_resource_version
        self._patch(meta["namespace"], meta["name"], body)

    def restore_deployment(self, snapshot: Any) -> None:
        """Put the replica count, the spread constraints and the required anti-affinity back to what ``snapshot`` held.

        Fields the snapshot did not have are set to null, which a strategic merge patch reads as "remove". Nothing else on
        the Deployment is touched, and no resourceVersion is sent: a restore is unconditional.
        """
        self._require_writes()
        anti = snapshot.pod_anti_affinity
        pod_spec: dict[str, Any] = {
            "topologySpreadConstraints": list(snapshot.topology_spread_constraints) or None,
            # No anti-affinity before: remove the key. Some before: set each list back, or remove the one that was absent.
            "affinity": {"podAntiAffinity": None if not anti else {
                REQUIRED_ANTI_AFFINITY: anti.get(REQUIRED_ANTI_AFFINITY) or None,
                PREFERRED_ANTI_AFFINITY: anti.get(PREFERRED_ANTI_AFFINITY) or None}},
        }
        self._patch(snapshot.namespace, snapshot.name,
                    {"spec": {"replicas": snapshot.replicas, "template": {"spec": pod_spec}}})
