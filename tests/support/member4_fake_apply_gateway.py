"""An in-memory cluster for Apply and Reset: the real service against Deployments that remember what was written. TEST-ONLY.

Rollout behaviour is scripted, not simulated: by default a Deployment is settled as soon as it is written, and a test that
wants the controller to lag, a pod to be unschedulable or a Deployment to be replaced says so with ``rollout_lag`` or the
other switches below.
"""

from copy import deepcopy
from dataclasses import replace

from app.integrations.kubernetes.client import AuthFailedError, ConnectionFailedError
from app.integrations.kubernetes.validation_gateway import (
    ConflictError,
    DeploymentRollout,
    ObservedDeployment,
    ObservedIdentity,
)


class FakeApplyGateway:
    def __init__(self, deployments: list[ObservedDeployment], *, uid: str | None = "uid-docker-desktop") -> None:
        self.deployments = {d.name: replace(d, uid=d.uid or f"uid-{d.name}") for d in deployments}
        self.uid = uid
        self.patches: list[dict] = []
        self.restores: list[str] = []
        self.fail_patch_after_applying: str | None = None      # the request is applied, then the connection drops
        self.fail_restore = False
        self.moved_since_read: str | None = None               # someone edits this Deployment between read and patch
        self.rollout_lag = 0                                    # reads that still show the controller behind after a write
        self.unreachable = False                                # every read fails like a lost connection
        self.credential_rejected = False                        # every read fails like an expired credential
        self.blocked_reason: str | None = None                  # the scheduler cannot place the new pods
        self.progress_failed: str | None = None                 # Progressing=False with this reason
        self.stale_old_pods = 0                                 # old-generation pods still owned after the write
        self.reads = 0
        self.cordoned: set[str] = set()                         # nodes that accept no new pods
        self.not_ready: set[str] = set()

    def _reachable(self) -> None:
        if self.credential_rejected:
            raise AuthFailedError("the cluster rejected the credential")
        if self.unreachable:
            raise ConnectionFailedError("ConnectError")

    def identity(self) -> ObservedIdentity:
        self._reachable()
        return ObservedIdentity("docker-desktop", "https://127.0.0.1:6443", self.uid, ("desktop-worker", "desktop-worker2"))

    def read_deployments(self, namespace: str):
        self._reachable()
        read = {n: deepcopy(d) for n, d in self.deployments.items() if d.namespace == namespace}
        if self.moved_since_read:
            moved = self.deployments[self.moved_since_read]
            self.deployments[moved.name] = replace(moved, resource_version=str(int(moved.resource_version) + 1))
        return read

    def patch_deployment(self, document, *, expect_resource_version: str) -> None:
        name = document["metadata"]["name"]
        current = self.deployments[name]
        if current.resource_version != expect_resource_version:
            raise ConflictError(409)
        spec = document.get("spec") or {}
        pod_spec = (spec.get("template") or {}).get("spec") or {}
        updated = replace(
            current, resource_version=str(int(current.resource_version) + 1), generation=current.generation + 1,
            replicas=spec.get("replicas", current.replicas),
            topology_spread_constraints=tuple(pod_spec.get("topologySpreadConstraints") or current.topology_spread_constraints),
            pod_anti_affinity=(pod_spec.get("affinity") or {}).get("podAntiAffinity") or current.pod_anti_affinity)
        self.deployments[name] = updated
        self.patches.append(document)
        if self.fail_patch_after_applying == name:
            raise ConnectionFailedError("ReadTimeout")

    def restore_deployment(self, snapshot) -> None:
        if self.fail_restore:
            raise ConnectionFailedError("ConnectError")
        current = self.deployments[snapshot.name]
        self.deployments[snapshot.name] = replace(
            current, resource_version=str(int(current.resource_version) + 1), generation=current.generation + 1,
            replicas=snapshot.replicas, topology_spread_constraints=tuple(snapshot.topology_spread_constraints),
            pod_anti_affinity=snapshot.pod_anti_affinity)
        self.restores.append(snapshot.name)

    def replace_deployment(self, name: str) -> None:
        """Delete and recreate a Deployment under the same name: same spec, another identity."""
        self.deployments[name] = replace(self.deployments[name], uid=f"uid-{name}-recreated")

    def read_rollout(self, namespace: str, names):
        self.reads += 1
        self._reachable()
        lagging = self.rollout_lag > 0
        if lagging:
            self.rollout_lag -= 1
        found = {}
        for name in names:
            deployment = self.deployments.get(name)
            if deployment is None or deployment.namespace != namespace:
                found[name] = DeploymentRollout(name, None, None, 0, None, None, None, missing=True)
                continue
            desired = deployment.replicas
            # While lagging, the controller has not observed the new generation, yet the OLD pods are all ready: that must
            # not count as a completed rollout.
            found[name] = DeploymentRollout(
                name=name, generation=deployment.generation,
                observed_generation=deployment.generation - 1 if lagging else deployment.generation,
                desired=desired, updated=desired, total=desired + self.stale_old_pods, ready=desired, available=desired,
                progress_failed_reason=self.progress_failed, uid=deployment.uid, blocked_reason=self.blocked_reason)
        return found

    def read_live_activity(self, namespace: str, names):
        self._reachable()
        nodes = ("desktop-worker", "desktop-worker2")
        found = []
        for name in names:
            d = self.deployments.get(name)
            if d is None or d.namespace != namespace:
                found.append({"name": name, "missing": True, "pods": []})
                continue
            found.append({"name": name, "missing": False, "generation": d.generation, "observedGeneration": d.generation,
                          "desired": d.replicas, "updated": d.replicas, "ready": d.replicas, "total": d.replicas,
                          "blockedReason": self.blocked_reason,
                          "pods": [{"name": f"{name}-{i}", "node": nodes[i % 2], "phase": "Running", "ready": True, "state": "Ready",
                                    "startedAt": None, "restarts": 0, "templateHash": f"hash{d.generation}", "version": "new"} for i in range(d.replicas)]})
        return found

    def read_node_states(self):
        self._reachable()
        return {n: {"ready": n not in self.not_ready, "schedulable": n not in self.cordoned}
                for n in ("desktop-worker", "desktop-worker2")}

    def rollout_complete(self, namespace: str, names) -> bool:
        return all(r.observed_generation >= r.generation and not r.missing for r in self.read_rollout(namespace, names).values())
