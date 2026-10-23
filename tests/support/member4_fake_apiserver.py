"""A fake Kubernetes API server for the validation gateway tests (TEST-ONLY).

An ``httpx.MockTransport`` handler that answers the reads the gateway makes with realistic API JSON, and records every
request, so a test can prove that a read-only gateway issued nothing but GETs. No network is opened and no kubeconfig of the
developer is read: the kubeconfig a test hands the gateway is a temporary file written by ``write_kubeconfig``.
"""

import base64
import json
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tls"
CA_B64 = base64.b64encode((FIXTURES / "ca.pem").read_bytes()).decode()
KUBE_SYSTEM_UID = "6f1e2c1a-0000-4000-8000-0000000000aa"
CONTROL_PLANE = "node-role.kubernetes.io/control-plane"
NAMESPACE = "bookinfo"


def write_kubeconfig(path: Path, *, server: str = "https://127.0.0.1:41234", context: str = "kind-resilience-validation") -> str:
    path.write_text(
        f"apiVersion: v1\nkind: Config\ncurrent-context: {context}\n"
        f"clusters:\n- name: k\n  cluster:\n    server: {server}\n    certificate-authority-data: {CA_B64}\n"
        "users:\n- name: u\n  user:\n    token: test-token-not-a-secret\n"
        f"contexts:\n- name: {context}\n  context:\n    cluster: k\n    user: u\n")
    return str(path)


def node_json(name: str, *, ready: str = "True", control_plane: bool = False, unschedulable: bool = False,
              cpu: str = "4", memory: str = "8Gi") -> dict:
    labels = {"kubernetes.io/hostname": name, **({CONTROL_PLANE: ""} if control_plane else {})}
    return {"metadata": {"name": name, "labels": labels}, "spec": {"unschedulable": True} if unschedulable else {},
            "status": {"allocatable": {"cpu": cpu, "memory": memory, "pods": "110"},
                       "conditions": [{"type": "Ready", "status": ready}]}}


class FakeApiServer:
    """State of a tiny cluster: nodes, and per service a Deployment with pods placed on named nodes."""

    def __init__(self, layout: dict[str, list[str]] | None = None, *, nodes: dict | None = None,
                 uid: str | None = KUBE_SYSTEM_UID, namespace: str = NAMESPACE, replicas: dict[str, int] | None = None) -> None:
        self.layout = layout or {"productpage": ["w1", "w2", "w3"], "details": ["w2", "w3"],
                                 "reviews": ["w1", "w1", "w1"], "ratings": ["w1", "w1"]}
        self.nodes = nodes if nodes is not None else {"cp": node_json("cp", control_plane=True), "w1": node_json("w1"),
                                                      "w2": node_json("w2"), "w3": node_json("w3")}
        self.uid, self.namespace, self.replicas = uid, namespace, replicas or {}
        self.requests: list[httpx.Request] = []
        self.patch_status = 200
        self.rollout_complete = True
        self.pod_ready: dict[str, bool] = {}
        self.pod_requests = {"cpu": "100m", "memory": "128Mi"}

    # -- bodies --------------------------------------------------------------------------------------------------

    def _deployment(self, service: str) -> dict:
        count = self.replicas.get(service, len(self.layout[service]))
        done = count if self.rollout_complete else count - 1
        return {"metadata": {"name": service, "namespace": self.namespace, "uid": f"dep-{service}",
                             "resourceVersion": f"rv-{service}-1", "generation": 3},
                "spec": {"replicas": count, "selector": {"matchLabels": {"app": service}},
                         "template": {"metadata": {"labels": {"app": service}}, "spec": {}}},
                "status": {"observedGeneration": 3, "replicas": count, "updatedReplicas": done, "readyReplicas": done, "availableReplicas": done}}

    def _replicaset(self, service: str) -> dict:
        return {"metadata": {"name": f"{service}-rs", "namespace": self.namespace, "uid": f"rs-{service}",
                             "ownerReferences": [{"kind": "Deployment", "name": service, "uid": f"dep-{service}", "controller": True}]}}

    def _pods(self) -> list[dict]:
        pods = []
        for service, placed in self.layout.items():
            for index, node in enumerate(placed):
                name = f"{service}-{index}"
                ready = self.pod_ready.get(name, True)
                pods.append({
                    "metadata": {"name": name, "namespace": self.namespace, "uid": f"pod-{name}", "labels": {"app": service},
                                 "ownerReferences": [{"kind": "ReplicaSet", "name": f"{service}-rs", "uid": f"rs-{service}",
                                                      "controller": True}]},
                    "spec": {"nodeName": node, "containers": [{"name": "c", "resources": {"requests": self.pod_requests}}]},
                    "status": {"phase": "Running" if ready else "Pending",
                               "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                               "containerStatuses": [{"restartCount": 0}]}})
        return pods

    def _routes(self) -> dict:
        ns = self.namespace
        return {
            "/api/v1/nodes": {"items": list(self.nodes.values())},
            f"/api/v1/namespaces/{ns}/pods": {"items": self._pods()},
            "/api/v1/pods": {"items": self._pods()},
            f"/apis/apps/v1/namespaces/{ns}/deployments": {"items": [self._deployment(s) for s in self.layout]},
            f"/apis/apps/v1/namespaces/{ns}/replicasets": {"items": [self._replicaset(s) for s in self.layout]},
            f"/api/v1/namespaces/{ns}/services": {"items": [
                {"metadata": {"name": s, "namespace": ns}, "spec": {"selector": {"app": s}}} for s in self.layout]},
            **({"/api/v1/namespaces/kube-system": {"metadata": {"name": "kube-system", "uid": self.uid}}} if self.uid else {}),
            **{f"/api/v1/nodes/{name}": node for name, node in self.nodes.items()},
        }

    # -- the transport ---------------------------------------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(self.patch_status, json={"kind": "Status"} if self.patch_status != 200 else {})
        if request.method != "GET":
            return httpx.Response(405, json={"kind": "Status", "reason": "MethodNotAllowed"})
        body = self._routes().get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"kind": "Status", "reason": "NotFound"})
        return httpx.Response(200, json=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def methods(self) -> set[str]:
        return {request.method for request in self.requests}

    def patches(self) -> list[tuple[str, dict, str]]:
        return [(r.url.path, json.loads(r.content), r.headers.get("content-type", "")) for r in self.requests if r.method == "PATCH"]
