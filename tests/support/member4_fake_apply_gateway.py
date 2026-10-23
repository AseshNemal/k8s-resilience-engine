"""An in-memory cluster for Apply and Reset: the real service against Deployments that remember what was written. TEST-ONLY."""

from copy import deepcopy
from dataclasses import replace

from app.integrations.kubernetes.client import ConnectionFailedError
from app.integrations.kubernetes.validation_gateway import ConflictError, ObservedDeployment, ObservedIdentity


class FakeApplyGateway:
    def __init__(self, deployments: list[ObservedDeployment], *, uid: str | None = "uid-docker-desktop") -> None:
        self.deployments = {d.name: d for d in deployments}
        self.uid = uid
        self.patches: list[dict] = []
        self.restores: list[str] = []
        self.fail_patch_after_applying: str | None = None      # the request is applied, then the connection drops
        self.fail_restore = False
        self.moved_since_read: str | None = None               # someone edits this Deployment between read and patch

    def identity(self) -> ObservedIdentity:
        return ObservedIdentity("docker-desktop", "https://127.0.0.1:6443", self.uid, ("desktop-worker", "desktop-worker2"))

    def read_deployments(self, namespace: str):
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
            current, resource_version=str(int(current.resource_version) + 1), replicas=snapshot.replicas,
            topology_spread_constraints=tuple(snapshot.topology_spread_constraints), pod_anti_affinity=snapshot.pod_anti_affinity)
        self.restores.append(snapshot.name)

    def rollout_complete(self, namespace: str, names) -> bool:
        return True
