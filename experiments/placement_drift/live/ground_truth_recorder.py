"""Records kubectl ground truth around real worker-node failures, for the model-accuracy evaluation.

One recording per failed node, in the layout app/engine/deployment_topology_modeling/ground_truth_evaluation
reads (the same as experiments/single-node-failure/concentrated-baseline, extended):

    <out>/<node>/pods.json nodes.json replicasets.json deployments.json <service>-service.yaml
                pod-placement.txt (kubectl get pods -o wide)  endpoints-before.txt (kubectl get endpointslices)
                owner-truth.txt   ("<deployment> <pod>" from `kubectl get pods -l <deployment selector>`)
    <out>/<node>/<node>-failure/nodes-after-detection.txt  endpointslices-after.json  pods.txt
                service-result.txt ("<service> SERVICE_AVAILABLE|SERVICE_UNAVAILABLE" per service)

A service is observed available after the failure when its EndpointSlices still list a ready endpoint:
that is the API server's own view, independent of the placement the model predicts from.

The node failure is real: the KinD node container is stopped with `docker stop` and started again
afterwards, always (also on error). Every pod on that node, in every namespace, is unavailable meanwhile.
"""
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from experiments.placement_drift.exceptions import ChaosSafetyError, ScenarioExecutionError
from experiments.placement_drift.live.kubectl import Kubectl


def docker(*args: str) -> None:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise ScenarioExecutionError(f"docker {' '.join(args)} failed: {result.stderr.strip()}")


@dataclass
class MixedSetup:
    """Replica layout that gives the recording both outcomes: services that one node failure takes
    down (one replica, or every replica on one node) and services that survive it."""
    single_replica: tuple[str, ...] = ("details-v1", "ratings-v1")
    concentrate: str | None = "reviews-v1"
    restore_replicas: int = 3


def service_availability(endpoint_slices: list[dict]) -> dict[str, bool]:
    """Service -> has at least one ready endpoint, from EndpointSlices (label kubernetes.io/service-name)."""
    available: dict[str, bool] = {}
    for es in endpoint_slices:
        service = (es["metadata"].get("labels") or {}).get("kubernetes.io/service-name")
        if not service:
            continue
        ready = any((ep.get("conditions") or {}).get("ready") for ep in es.get("endpoints") or [])
        available[service] = available.get(service, False) or ready
    return available


def ready_endpoints_on(endpoint_slices: list[dict], node: str) -> int:
    return sum(
        1 for es in endpoint_slices for ep in es.get("endpoints") or []
        if ep.get("nodeName") == node and (ep.get("conditions") or {}).get("ready")
    )


class GroundTruthRecorder:
    def __init__(self, kube: Kubectl, deployments: list[str], out_dir: Path,
                 run_docker: Callable[..., None] = docker, poll_s: float = 3.0,
                 detect_timeout_s: int = 180, recover_timeout_s: int = 300):
        self.kube = kube
        self.deployments = deployments
        self.out_dir = out_dir
        self.docker = run_docker
        self.poll_s = poll_s
        self.detect_timeout_s = detect_timeout_s
        self.recover_timeout_s = recover_timeout_s

    # --- setup --------------------------------------------------------------------------------

    def apply_mixed_setup(self, setup: MixedSetup) -> None:
        for deployment in setup.single_replica:
            self.kube.scale(deployment, 1)
        if setup.concentrate:
            # Only one worker accepts new pods while the Deployment restarts, so all its replicas land there.
            workers = self.kube.worker_nodes()
            others = workers[1:]
            try:
                for node in others:
                    self.kube.cordon(node)
                self.kube.rollout_restart(setup.concentrate)
                self.kube.wait_until_stable(self.deployments, timeout_s=self.recover_timeout_s)
            finally:
                for node in others:
                    self.kube.uncordon(node)
        self.kube.wait_until_stable(self.deployments, timeout_s=self.recover_timeout_s)

    def restore(self, setup: MixedSetup) -> None:
        for deployment in setup.single_replica:
            self.kube.scale(deployment, setup.restore_replicas)
        if setup.concentrate:
            self.kube.rollout_restart(setup.concentrate)  # spread again by the scheduler
        self.kube.wait_until_stable(self.deployments, timeout_s=self.recover_timeout_s)

    # --- one recording --------------------------------------------------------------------------

    def record_before(self, case: Path) -> None:
        case.mkdir(parents=True, exist_ok=True)
        k = self.kube
        (case / "pods.json").write_text(k.run("get", "pods", "-o", "json"), encoding="utf-8")
        (case / "nodes.json").write_text(k.run("get", "nodes", "-o", "json", namespaced=False), encoding="utf-8")
        (case / "replicasets.json").write_text(k.run("get", "replicasets", "-o", "json"), encoding="utf-8")
        deployments_text = k.run("get", "deployments", "-o", "json")
        (case / "deployments.json").write_text(deployments_text, encoding="utf-8")
        for service in json.loads(k.run("get", "services", "-o", "json"))["items"]:
            name = service["metadata"]["name"]
            (case / f"{name}-service.yaml").write_text(k.run("get", "service", name, "-o", "yaml"), encoding="utf-8")
        (case / "pod-placement.txt").write_text(k.run("get", "pods", "-o", "wide"), encoding="utf-8")
        (case / "endpoints-before.txt").write_text(k.run("get", "endpointslices"), encoding="utf-8")
        (case / "owner-truth.txt").write_text(self._owner_truth(json.loads(deployments_text)["items"]), encoding="utf-8")

    def _owner_truth(self, deployments: list[dict]) -> str:
        """The API server's answer to "which pods does this Deployment select?" (matchLabels only)."""
        lines = []
        for d in deployments:
            selector = (d["spec"].get("selector") or {}).get("matchLabels") or {}
            if not selector:
                continue
            query = ",".join(f"{k}={v}" for k, v in sorted(selector.items()))
            for item in json.loads(self.kube.run("get", "pods", "-l", query, "-o", "json"))["items"]:
                lines.append(f"{d['metadata']['name']} {item['metadata']['name']}\n")
        return "".join(lines)

    def fail_node(self, case: Path, node: str) -> dict[str, bool]:
        if node not in self.kube.worker_nodes():
            raise ChaosSafetyError(f"Refusing to stop '{node}': not a worker node of this cluster.")
        failure = case / f"{node}-failure"
        failure.mkdir(parents=True, exist_ok=True)
        self.docker("stop", node)
        try:
            self._wait(lambda: self._node_status(node) == "NotReady", self.detect_timeout_s, f"{node} NotReady")
            self._wait(lambda: ready_endpoints_on(self._endpoint_slices(), node) == 0, self.detect_timeout_s,
                       f"endpoints on {node} not ready")
            slices = self._endpoint_slices()
            (failure / "nodes-after-detection.txt").write_text(self.kube.run("get", "nodes", "-o", "wide", namespaced=False), encoding="utf-8")
            (failure / "endpointslices-after.json").write_text(json.dumps({"items": slices}, indent=2), encoding="utf-8")
            (failure / "pods.txt").write_text(self.kube.run("get", "pods", "-o", "wide"), encoding="utf-8")
            outcome = service_availability(slices)
            (failure / "service-result.txt").write_text(
                "".join(f"{s} {'SERVICE_AVAILABLE' if ok else 'SERVICE_UNAVAILABLE'}\n" for s, ok in sorted(outcome.items())),
                encoding="utf-8")
            return outcome
        finally:
            self.docker("start", node)
            self._wait(lambda: self._node_status(node) == "Ready", self.recover_timeout_s, f"{node} Ready again")
            self.kube.wait_until_stable(self.deployments, timeout_s=self.recover_timeout_s)

    # --- helpers ----------------------------------------------------------------------------------

    def _endpoint_slices(self) -> list[dict]:
        return self.kube.get_json("endpointslices")["items"]

    def _node_status(self, node: str) -> str:
        for n in self.kube.get_json("nodes", namespaced=False)["items"]:
            if n["metadata"]["name"] == node:
                ready = next((c for c in n["status"].get("conditions", []) if c["type"] == "Ready"), {})
                return "Ready" if ready.get("status") == "True" else "NotReady"
        return "Missing"

    def _wait(self, condition: Callable[[], bool], timeout_s: int, what: str) -> None:
        start = time.monotonic()
        while not condition():
            if time.monotonic() - start > timeout_s:
                raise ScenarioExecutionError(f"Timed out after {timeout_s}s waiting for {what}.")
            time.sleep(self.poll_s)
