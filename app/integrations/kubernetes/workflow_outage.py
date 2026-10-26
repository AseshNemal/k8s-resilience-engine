"""Take one Deployment of an opted-in test namespace down to zero replicas and back, for Component 1's outage tests.

Component 1 labels a service mandatory, optional or supporting only from a controlled outage: the workflow is run while the
service is unavailable and the outcomes are compared with a baseline. On Kubernetes, "unavailable" is a Deployment scaled to
zero with no pod left. This module is the only place Component 1 writes to a cluster, and it is deliberately narrow:

- One namespace, named by the backend's own setting (never by a request), and only if that namespace carries the label
  ``resilience.research/workflow-outages=allowed``. The label is the cluster owner's opt-in; nothing here sets it.
- One kind of write: a strategic-merge PATCH of an existing Deployment that sets ``spec.replicas`` and a recovery annotation,
  conditional on the ``resourceVersion`` that was read, so a Deployment changed in between is refused, not overwritten.
- The replica count before the outage is stored ON the Deployment (``ORIGINAL_REPLICAS_ANNOTATION``), so an outage left
  behind by a crash is visible to anyone and can be put back without this process's memory.

It imports nothing from ``app.engine``; the engine decides when to call it and what counts as verified.
"""

from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote, urlencode

import httpx

from app.integrations.kubernetes import rest

OPT_IN_LABEL = "resilience.research/workflow-outages"
OPT_IN_VALUE = "allowed"
ORIGINAL_REPLICAS_ANNOTATION = "resilience.research/workflow-outage-original-replicas"
STRATEGIC_MERGE = "application/strategic-merge-patch+json"
DEFAULT_TIMEOUT_SECONDS = 10.0


class OutageNotPermittedError(ValueError):
    """The namespace or Deployment is not one this module may take down."""


class OutageClusterError(RuntimeError):
    """The cluster could not be read or refused the change; the message never carries a credential or a path."""


@dataclass(frozen=True)
class DeploymentState:
    """What the API server says about one Deployment and the pods its selector matches."""

    name: str
    namespace: str
    uid: str
    resource_version: str
    replicas: int                     # spec.replicas: what the Deployment asks for
    ready_replicas: int               # status.readyReplicas (absent means 0)
    pod_count: int                    # pods matching the selector, terminating ones included
    original_replicas: int | None     # from the recovery annotation; None when no outage is recorded on it

    @property
    def down(self) -> bool:
        """Verified unavailable: nothing asked for and no pod left, not even one still terminating."""
        return self.replicas == 0 and self.ready_replicas == 0 and self.pod_count == 0

    @property
    def restored(self) -> bool:
        return self.original_replicas is None and self.replicas > 0 and self.ready_replicas >= self.replicas

    def evidence(self) -> str:
        return (f"Kubernetes API: deployment={self.namespace}/{self.name} uid={self.uid} "
                f"specReplicas={self.replicas} readyReplicas={self.ready_replicas} matchingPods={self.pod_count}")


class _Client(rest.RestClient):
    """``RestClient`` plus the one PATCH this module sends. The shared client stays GET/POST only."""

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


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class KubernetesDeploymentScaler:
    """Scale Deployments of ONE opted-in namespace of the backend's connected cluster."""

    def __init__(
        self,
        namespace: str,
        *,
        config_file: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not isinstance(namespace, str) or not namespace.strip():
            raise OutageNotPermittedError("No outage namespace is configured for workflow experiments")
        self.namespace = namespace.strip()
        self._config_file = config_file
        self._timeout = timeout
        self._transport = transport  # a seam for tests; production always uses the network

    def _open(self) -> _Client:
        raw = self._config_file or os.environ.get("KUBECONFIG") or "~/.kube/config"
        paths = [p for p in (os.path.expanduser(item) for item in raw.split(os.pathsep) if item) if os.path.isfile(p)]
        if not paths:
            raise OutageClusterError("No cluster is connected")
        try:
            return _Client(rest.load_connection(paths), self._timeout, transport=self._transport)
        except rest.KubeconfigError as error:
            raise OutageClusterError(f"The cluster connection is not usable: {error}") from None

    def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        client = self._open()
        try:
            if method == "PATCH":
                return client.patch_json(path, body or {})
            return client.get_json(path)
        except rest.HttpStatusError as error:
            if error.status == 404:
                raise OutageNotPermittedError("The Deployment or namespace does not exist in the connected cluster") from None
            if error.status == 409:
                raise OutageClusterError("The Deployment changed while it was being scaled; nothing was overwritten") from None
            if error.status in {401, 403}:
                raise OutageClusterError("The cluster credential may not read or scale this Deployment") from None
            raise OutageClusterError(f"The Kubernetes API answered HTTP {error.status}") from None
        except rest.TransportFailure as error:
            raise OutageClusterError(f"The Kubernetes API could not be reached ({error.name})") from None
        finally:
            client.close()

    def _deployment_path(self, name: str) -> str:
        return f"/apis/apps/v1/namespaces/{quote(self.namespace, safe='')}/deployments/{quote(name, safe='')}"

    def require_opt_in(self) -> None:
        body = self._call("GET", f"/api/v1/namespaces/{quote(self.namespace, safe='')}")
        labels = ((body or {}).get("metadata") or {}).get("labels") or {}
        if labels.get(OPT_IN_LABEL) != OPT_IN_VALUE:
            raise OutageNotPermittedError(
                f"Namespace {self.namespace} has not opted in to workflow outage tests "
                f"(label {OPT_IN_LABEL}={OPT_IN_VALUE} is missing)"
            )

    def inspect(self, name: str) -> DeploymentState:
        body = self._call("GET", self._deployment_path(name)) or {}
        meta = body.get("metadata") or {}
        spec = body.get("spec") or {}
        selector = (spec.get("selector") or {}).get("matchLabels") or {}
        if not isinstance(selector, Mapping) or not selector or (spec.get("selector") or {}).get("matchExpressions"):
            # Without a plain label selector the pods cannot be counted reliably, so the outage could not be verified.
            raise OutageNotPermittedError(f"Deployment {name} has no plain label selector; its pods cannot be verified")
        query = urlencode({"labelSelector": ",".join(f"{key}={value}" for key, value in sorted(selector.items()))})
        pods = self._call("GET", f"/api/v1/namespaces/{quote(self.namespace, safe='')}/pods?{query}") or {}
        raw_original = (meta.get("annotations") or {}).get(ORIGINAL_REPLICAS_ANNOTATION)
        original = int(raw_original) if isinstance(raw_original, str) and raw_original.isdigit() else None
        return DeploymentState(
            name=str(meta.get("name") or name),
            namespace=self.namespace,
            uid=str(meta.get("uid") or ""),
            resource_version=str(meta.get("resourceVersion") or ""),
            replicas=_int(spec.get("replicas")),
            ready_replicas=_int((body.get("status") or {}).get("readyReplicas")),
            pod_count=len((pods.get("items") if isinstance(pods, dict) else None) or []),
            original_replicas=original,
        )

    def take_down(self, state: DeploymentState) -> None:
        """Scale to zero, recording the replica count it had, only if the Deployment is unchanged since ``state``."""
        if state.original_replicas is not None:
            raise OutageNotPermittedError(f"Deployment {state.name} already carries an unrecovered outage")
        if state.replicas < 1:
            raise OutageNotPermittedError(f"Deployment {state.name} is already scaled to zero; restore it first")
        self._call("PATCH", self._deployment_path(state.name), {
            "metadata": {"resourceVersion": state.resource_version,
                         "annotations": {ORIGINAL_REPLICAS_ANNOTATION: str(state.replicas)}},
            "spec": {"replicas": 0},
        })

    def restore(self, state: DeploymentState) -> int:
        """Put back the replica count recorded on the Deployment and remove the record. Returns that count."""
        if state.original_replicas is None or state.original_replicas < 1:
            raise OutageNotPermittedError(f"Deployment {state.name} has no recorded outage to restore")
        self._call("PATCH", self._deployment_path(state.name), {
            "metadata": {"resourceVersion": state.resource_version,
                         "annotations": {ORIGINAL_REPLICAS_ANNOTATION: None}},
            "spec": {"replicas": state.original_replicas},
        })
        return state.original_replicas

    def interrupted(self) -> list[str]:
        """Deployments of the namespace that still carry an outage record (for example after a backend restart)."""
        body = self._call("GET", f"/apis/apps/v1/namespaces/{quote(self.namespace, safe='')}/deployments") or {}
        return sorted(
            str((item.get("metadata") or {}).get("name"))
            for item in body.get("items") or []
            if ORIGINAL_REPLICAS_ANNOTATION in (((item.get("metadata") or {}).get("annotations")) or {})
        )
