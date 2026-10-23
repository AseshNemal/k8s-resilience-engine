"""Stop and restart one worker node of a kind cluster, abruptly, under its own authorization. Never the control plane.

What it does. ``inject`` runs ``docker kill`` on the container that is the worker node: SIGKILL, so the node's kubelet and
network vanish at once with no warning to the control plane, which then notices only after its node-monitor grace period.
That is a node failure. It is not ``docker stop``: a kind node runs systemd, and a stop signal lets it shut its services
down in order, which is gentler than a crash and would understate the failure. It is not cordon or drain either, which are
graceful evictions of a healthy node. ``restore`` runs ``docker start``.

What has to be true before it acts, each refused with a code and no process started:

* it was ARMED with an authorization of its own (action ``STOP_KIND_WORKER_CONTAINER``), bound to the plan digest and the
  target, naming a person, for ONE node and ONE injection (a second kill needs a second authorization). The authorization the run needs to inject its failure
  (``FAIL_NODE_IN_TEST_CLUSTER``) is a different one and does not arm this;
* the node is one of the worker names given at construction, all of which must be ``<cluster>-worker[N]`` of the designated
  kind cluster. A control-plane name cannot even be constructed, and the cluster named ``desktop`` (the Docker Desktop
  cluster) or ``docker-desktop`` is refused outright;
* the container itself carries kind's labels for that cluster and the ``worker`` role, and is running;
* no earlier recovery record is still pending;
* a recovery record is written to disk (atomically, synced) BEFORE the kill. If the kill fails the record stays pending,
  because the state of the node is then unknown.

``restore`` acts only on a pending record of its own; with none it does nothing, so it is safe to call when the node was
never stopped. It re-verifies the container before starting it. A restore that fails raises with the exact command to run
by hand, and the record stays pending. ``python -m app.integrations.kind.recovery`` is the same procedure from a terminal.

This module has never stopped a real container; its tests drive a scripted ``docker``. It imports nothing from the engine.
"""

import re
import uuid
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from app.integrations.kind.recovery import (
    DockerCli,
    Runner,
    now_iso,
    pending_records,
    restore_container,
    save_record,
    subprocess_runner,
)

STOP_ACTION = "STOP_KIND_WORKER_CONTAINER"
REFUSED_CLUSTERS = frozenset({"desktop", "docker-desktop"})
CLUSTER_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class FaultKind(str, Enum):
    NODE_STOP = "NODE_STOP"


class FaultRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


class KindWorkerFaultInjector:
    kind = FaultKind.NODE_STOP
    evidence_class = "REAL_CLUSTER"
    method = ("docker kill <container>: SIGKILL to the kind worker node's container (an abrupt node failure, not a drain); "
              "restored with docker start <container>")

    def __init__(self, kind_cluster: str, workers: Sequence[str], recovery_dir: str | Path, *,
                 runner: Runner = subprocess_runner) -> None:
        cluster = (kind_cluster or "").strip().lower()
        if not CLUSTER_NAME.match(cluster) or cluster in REFUSED_CLUSTERS:
            raise FaultRefused("CLUSTER_REFUSED", f"{kind_cluster!r} cannot be a validation cluster; the Docker Desktop cluster "
                               f"and an unnamed one are never stopped")
        allowed = re.compile(rf"^{re.escape(cluster)}-worker[0-9]*$")
        if not workers or any(not allowed.match(name or "") for name in workers):
            raise FaultRefused("NODE_NOT_ALLOWED", f"only workers named {cluster}-worker[N] of the designated kind cluster can be "
                               f"named, never a control-plane node: {list(workers)!r}")
        self.cluster, self._workers = cluster, frozenset(workers)
        self._dir, self._docker = Path(recovery_dir), DockerCli(runner)
        self._grants: list[tuple[str, str, str]] = []      # (node, authorized_by, plan_digest), each good for ONE injection

    # -- authorization -------------------------------------------------------------------------------------------------

    def arm(self, authorization: Any, *, plan_digest: str, target: str, failed_node: str) -> None:
        """Allow ONE more injection on ``failed_node``, on the strength of a separately bound authorization.

        Every kill needs a grant of its own: an experiment with a measured baseline kills the node twice, so it is armed twice.
        """
        if (authorization is None or not (getattr(authorization, "confirmed_by", "") or "").strip()
                or getattr(authorization, "action", None) != STOP_ACTION
                or getattr(authorization, "plan_digest", None) != plan_digest
                or getattr(authorization, "target", None) != target):
            raise FaultRefused("AUTHORIZATION_MISMATCH", f"the container of {failed_node} is stopped only on a named person's "
                               f"{STOP_ACTION} authorization bound to this plan and target")
        if failed_node not in self._workers:
            raise FaultRefused("NODE_NOT_ALLOWED", f"{failed_node!r} is not one of the workers this injector was built for")
        self._grants.append((failed_node, authorization.confirmed_by.strip(), plan_digest))

    # -- the fault -----------------------------------------------------------------------------------------------------

    def inject(self, node: str) -> None:
        grant = next((g for g in self._grants if g[0] == node), None)
        if grant is None:
            raise FaultRefused("NOT_ARMED", f"nothing authorizes stopping {node!r}: each injection needs an authorization of "
                               f"its own for that node")
        pending = pending_records(self._dir)
        if pending:
            raise FaultRefused("RECOVERY_PENDING", f"{len(pending)} earlier recovery record(s) are not restored "
                               f"({pending[0]['container']}); restore them first with python -m app.integrations.kind.recovery")
        info, problem = self._docker.is_kind_worker_of(node, self.cluster)
        if problem == "CONTAINER_NOT_FOUND":
            raise FaultRefused(problem, f"there is no container {node!r}")
        if problem is not None:
            raise FaultRefused(problem, f"{node!r} is not a worker of kind cluster {self.cluster!r} (kind's labels say "
                               f"{ {k: v for k, v in info['labels'].items() if k.startswith('io.x-k8s.kind')} })")
        if not info["running"]:
            raise FaultRefused("CONTAINER_NOT_RUNNING", f"{node!r} is not running, so stopping it would prove nothing")

        _, authorized_by, plan_digest = grant
        record = {"schema": 1, "recordId": f"{self.cluster}-{node}-{uuid.uuid4().hex[:8]}", "cluster": self.cluster,
                  "container": node, "node": node, "authorizedBy": authorized_by, "planDigest": plan_digest,
                  "state": "INJECTING", "createdAt": now_iso(), "stoppedAt": None, "restoredAt": None, "lastError": None,
                  "method": self.method, "restoreCommand": ["docker", "start", node]}
        save_record(self._dir, record)                  # on disk before anything is killed
        self._grants.remove(grant)                      # one authorization, one injection, even if the kill fails
        killed = self._docker.run("kill", node)
        if killed.returncode != 0:
            save_record(self._dir, {**record, "lastError": killed.stderr.strip() or f"exit {killed.returncode}"})
            raise FaultRefused("KILL_FAILED", f"docker kill {node} failed; the node's state is unknown, so the recovery record "
                               f"stays pending: {killed.stderr.strip()}")
        save_record(self._dir, {**record, "state": "STOPPED", "stoppedAt": now_iso()})

    def restore(self, node: str) -> None:
        if node not in self._workers:
            raise FaultRefused("NODE_NOT_ALLOWED", f"{node!r} is not one of the workers this injector was built for")
        mine = [r for r in pending_records(self._dir) if r["container"] == node and r["cluster"] == self.cluster]
        if not mine:
            return          # this injector never stopped it (or it is already restored): nothing to undo
        problem = restore_container(self._docker, mine[-1], self._dir)
        if problem is not None:
            raise FaultRefused("RESTORE_FAILED", f"{node} was not restored ({problem}). Start it by hand: docker start {node}")
