"""The only code in the live experiments that changes a cluster.

Every command is pinned to one kubeconfig context with `--context`, so the user's current context
(e.g. a demo cluster) is never touched by accident. Only local KinD contexts are accepted and
system namespaces are refused.
"""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from experiments.placement_drift.constants import PROTECTED_NAMESPACES
from experiments.placement_drift.exceptions import ChaosSafetyError, ScenarioExecutionError


class Kubectl:
    def __init__(self, context: str, namespace: str, command_timeout_s: int = 180):
        if not context.startswith("kind-"):
            raise ChaosSafetyError(f"Refusing context '{context}': live experiments only run on local KinD clusters.")
        if namespace in PROTECTED_NAMESPACES:
            raise ChaosSafetyError(f"Refusing protected namespace '{namespace}'.")
        self.context = context
        self.namespace = namespace
        self.command_timeout_s = command_timeout_s
        self.log: list[list[str]] = []

    def run(self, *args: str, namespaced: bool = True, check: bool = True) -> str:
        cmd = ["kubectl", "--context", self.context]
        if namespaced:
            cmd += ["--namespace", self.namespace]
        cmd += list(args)
        self.log.append(cmd)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.command_timeout_s)
        if check and result.returncode != 0:
            raise ScenarioExecutionError(f"{' '.join(cmd)} failed: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout

    def get_json(self, *args: str, namespaced: bool = True) -> dict[str, Any]:
        return json.loads(self.run("get", *args, "-o", "json", namespaced=namespaced))

    def write_pinned_kubeconfig(self) -> Path:
        """Writes a kubeconfig holding only this context (as its current-context) to a private temp file.

        The Component 2 collector reads the kubeconfig's current context, so pointing $KUBECONFIG at
        this file makes it observe the experiment cluster, never the user's current one. The file holds
        the local KinD credential: it is created with owner-only permissions and the caller deletes it.
        """
        text = self.run("config", "view", "--minify", "--flatten", namespaced=False)
        descriptor, name = tempfile.mkstemp(prefix="kubeconfig-experiment-", suffix=".yaml")
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(name, 0o600)
        return Path(name)

    # --- workload ---------------------------------------------------------------------------

    def apply(self, manifest_path: str) -> None:
        self.run("apply", "-f", manifest_path, namespaced=False)

    def delete_experiment_workloads(self) -> None:
        """Deletes everything this experiment created in its namespace (namespace itself is kept)."""
        self.run("delete", "deployment,service", "--all", "--ignore-not-found", "--wait=true")

    def scale(self, deployment: str, replicas: int) -> None:
        self.run("scale", f"deployment/{deployment}", f"--replicas={replicas}")

    def rollout_restart(self, deployment: str) -> None:
        self.run("rollout", "restart", f"deployment/{deployment}")

    def delete_pod(self, pod: str) -> None:
        self.run("delete", "pod", pod, "--wait=false")

    # --- nodes ------------------------------------------------------------------------------

    def worker_nodes(self) -> list[str]:
        nodes = self.get_json("nodes", namespaced=False)["items"]
        return sorted(
            n["metadata"]["name"] for n in nodes
            if "node-role.kubernetes.io/control-plane" not in (n["metadata"].get("labels") or {})
        )

    def cordon(self, node: str) -> None:
        self._require_worker(node)
        self.run("cordon", node, namespaced=False)

    def uncordon(self, node: str) -> None:
        self.run("uncordon", node, namespaced=False)

    def drain(self, node: str) -> None:
        self._require_worker(node)
        self.run("drain", node, "--ignore-daemonsets", "--delete-emptydir-data",
                 f"--timeout={self.command_timeout_s - 10}s", namespaced=False)

    def _require_worker(self, node: str) -> None:
        if node not in self.worker_nodes():
            raise ChaosSafetyError(f"Refusing to cordon/drain '{node}': not a worker node.")

    # --- waiting ----------------------------------------------------------------------------

    def wait_until_stable(self, deployments: list[str], timeout_s: int = 180, poll_s: float = 2.0) -> float:
        """Waits until every deployment has all replicas updated and ready and no pod in the
        namespace is still terminating. Returns the seconds waited."""
        start = time.monotonic()
        while True:
            if self._is_stable(deployments):
                return round(time.monotonic() - start, 2)
            if time.monotonic() - start > timeout_s:
                raise ScenarioExecutionError(f"Workload not stable after {timeout_s}s: {deployments}")
            time.sleep(poll_s)

    def _is_stable(self, deployments: list[str]) -> bool:
        by_name = {d["metadata"]["name"]: d for d in self.get_json("deployments")["items"]}
        for name in deployments:
            d = by_name.get(name)
            if d is None:
                return False
            want = d["spec"].get("replicas", 1)
            status = d.get("status", {})
            if d["metadata"].get("generation") != status.get("observedGeneration"):
                return False
            if not (status.get("replicas", 0) == status.get("updatedReplicas", 0)
                    == status.get("readyReplicas", 0) == want):
                return False
        pods = self.get_json("pods")["items"]
        return not any(p["metadata"].get("deletionTimestamp") for p in pods)

    def wait_until_no_pods(self, label_selector: str, timeout_s: int = 120, poll_s: float = 2.0) -> None:
        start = time.monotonic()
        while self.get_json("pods", "-l", label_selector)["items"]:
            if time.monotonic() - start > timeout_s:
                raise ScenarioExecutionError(f"Pods '{label_selector}' still present after {timeout_s}s")
            time.sleep(poll_s)
