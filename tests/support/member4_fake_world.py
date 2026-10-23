"""A kind cluster, its docker and its application, in memory and coupled the way the real ones are (TEST-ONLY).

``docker kill`` of a node's container turns that node NotReady and its pods unready; ``docker start`` brings it back; a PATCH of a
Deployment re-places its pods; the application answers an HTTP request only while every mandatory service has a pod on a node that
is up. Nothing here opens a network connection, runs a process, or reads a kubeconfig of the developer. Because everything under
the real gateway, injector and probe is a double, a run over this world is a TEST_DOUBLE run.
"""

import json
import re

import httpx

from tests.support.member4_fake_apiserver import KUBE_SYSTEM_UID, FakeApiServer, node_json, write_kubeconfig
from tests.support.member4_fake_docker import CLUSTER, FakeDocker, labels
from tests.support.member4_fake_gateway import FakeClock, FakeWorkflowBackend
from tests.support.member4_harness import OFFLINE_NAMESPACE

CONTEXT = "kind-resilience-validation"
SERVER = "https://127.0.0.1:41234"


class StatefulApi(FakeApiServer):
    """The fake API server, with Deployments that remember their version and a PATCH that moves pods."""

    def __init__(self, workers, *, spreads=True, **kwargs):
        super().__init__(**kwargs)
        self.workers, self.spreads = list(workers), spreads
        self.original = {service: list(nodes) for service, nodes in self.layout.items()}
        self.version = {service: 1 for service in self.layout}
        self.constraints = {}

    def ready(self, node: str) -> bool:
        return self.nodes[node]["status"]["conditions"][0]["status"] == "True"

    def _deployment(self, service):
        body = super()._deployment(service)
        body["metadata"]["resourceVersion"] = f"rv-{service}-{self.version[service]}"
        if service in self.constraints:
            body["spec"]["template"]["spec"]["topologySpreadConstraints"] = self.constraints[service]
        return body

    def _pods(self):
        pods = super()._pods()
        for pod in pods:
            if not self.ready(pod["spec"]["nodeName"]):
                pod["status"]["conditions"] = [{"type": "Ready", "status": "False"}]
        return pods

    def __call__(self, request: httpx.Request) -> httpx.Response:
        match = re.match(r"^/apis/apps/v1/namespaces/[^/]+/deployments/([^/]+)$", request.url.path)
        if request.method != "PATCH" or not match:
            return super().__call__(request)
        self.requests.append(request)
        service, body = match.group(1), json.loads(request.content)
        if self.patch_status != 200:
            return httpx.Response(self.patch_status, json={"kind": "Status"})
        expected = body.get("metadata", {}).get("resourceVersion")
        if expected is not None and expected != f"rv-{service}-{self.version[service]}":
            return httpx.Response(409, json={"kind": "Status", "reason": "Conflict"})
        pod_spec = (body.get("spec", {}).get("template") or {}).get("spec") or {}
        constraints = pod_spec.get("topologySpreadConstraints", "absent")
        count = body.get("spec", {}).get("replicas") or len(self.layout[service])
        self.version[service] += 1
        if constraints is None:                                     # a restore: the controller recreates the original pods
            self.constraints.pop(service, None)
            self.layout[service] = list(self.original[service])
        else:
            if constraints != "absent":
                self.constraints[service] = constraints
            if self.spreads and constraints != "absent":
                self.layout[service] = [self.workers[i % len(self.workers)] for i in range(count)]
        return httpx.Response(200, json={})


class FakeWorld:
    WORKERS = (f"{CLUSTER}-worker", f"{CLUSTER}-worker2", f"{CLUSTER}-worker3")
    CONTROL_PLANE = f"{CLUSTER}-control-plane"

    def __init__(self, tmp_path, *, spreads=True, broken=()):
        w1, w2, w3 = self.WORKERS
        nodes = {self.CONTROL_PLANE: node_json(self.CONTROL_PLANE, control_plane=True),
                 **{name: node_json(name) for name in self.WORKERS}}
        self.api = StatefulApi(self.WORKERS, spreads=spreads, namespace=OFFLINE_NAMESPACE, nodes=nodes,
                               layout={"productpage": [w1, w2, w3], "details": [w2, w3], "reviews": [w1] * 3, "ratings": [w1] * 2})
        containers = {self.CONTROL_PLANE: {"labels": labels(role="control-plane"), "running": True},
                      **{name: {"labels": labels(), "running": True} for name in self.WORKERS},
                      "desktop-worker": {"labels": labels("desktop"), "running": True}}
        self.docker = FakeDocker(containers, on_state=self._container_changed)
        self.clock = FakeClock()
        self.kubeconfig = write_kubeconfig(tmp_path / "validation.kubeconfig", server=SERVER, context=CONTEXT)
        self.recovery_dir = str(tmp_path / "recovery")
        self.broken = set(broken)
        self.probe_transport = httpx.MockTransport(self._application)
        self.requests_to_application = 0

    def _container_changed(self, name: str, running: bool) -> None:
        if name in self.api.nodes:
            self.api.nodes[name]["status"]["conditions"][0]["status"] = "True" if running else "False"

    def _application(self, request: httpx.Request) -> httpx.Response:
        self.requests_to_application += 1
        mandatory = ("productpage", "details", "reviews")
        alive = all(svc not in self.broken and any(self.api.ready(node) for node in self.api.layout[svc]) for svc in mandatory)
        return httpx.Response(200, text=FakeWorkflowBackend.OK if alive else FakeWorkflowBackend.DEGRADED)

    def layout(self):
        return {service: list(nodes) for service, nodes in self.api.layout.items()}
