"""Can the worker containers of a kind cluster be identified and stopped for a valid failure window?  A check, before any fault.

A node failure only counts when the node STAYS down for the whole window. Docker restarts a container after it exits if its
restart policy says so, and kind's node containers are created with ``on-failure``: a killed node may come back on its own, in
seconds, which would cut the failure short and make every result meaningless. Whether ``docker kill`` triggers that restart on
a given Docker version is not something to assume, so the check reports three separate things per worker:

* IDENTIFIABLE: the container exists and kind's own labels say it is a worker of this cluster (not a control plane, not
  another application's container);
* CONTROLLABLE: it is running, so a stop and a start through the restricted proxy are meaningful;
* WINDOW: the restart policy. ``no`` means nothing restarts it, so a valid window is expected. Anything else is UNVERIFIED: the
  window is valid only if one supervised stop is actually observed to stay down. The loop also detects a node that came back by
  itself and refuses to count that experiment, so an UNVERIFIED policy cannot silently produce a false measurement.

``window_verified`` is True once a previous run on this cluster completed an experiment with a valid window (a PASS or FAIL
verdict, which the executor only produces after the node stayed NotReady to the end). Until then an UNVERIFIED cluster is limited
to the Smoke Test, which is the supervised check.

Reads only. It never stops or starts anything.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.integrations.kind.recovery import KIND_CLUSTER_LABEL, KIND_ROLE_LABEL, DockerCli

POLICY_OK = frozenset({"no"})


@dataclass(frozen=True)
class WorkerReadiness:
    container: str
    identifiable: bool
    controllable: bool
    restart_policy: str | None
    window: str                  # OK | UNVERIFIED | UNREADABLE
    problem: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"container": self.container, "identifiable": self.identifiable, "controllable": self.controllable,
                "restartPolicy": self.restart_policy, "window": self.window, "problem": self.problem}


@dataclass(frozen=True)
class FaultReadiness:
    cluster: str
    workers: tuple[WorkerReadiness, ...]
    window_verified: bool

    @property
    def identifiable(self) -> bool:
        return bool(self.workers) and all(w.identifiable for w in self.workers)

    @property
    def controllable(self) -> bool:
        return bool(self.workers) and all(w.controllable for w in self.workers)

    @property
    def window(self) -> str:
        windows = {w.window for w in self.workers}
        if not windows or "UNREADABLE" in windows:
            return "UNREADABLE"
        if windows == {"OK"} or self.window_verified:
            return "OK"
        return "UNVERIFIED"

    @property
    def smoke_only(self) -> bool:
        """True while the only thing allowed is the supervised Smoke Test (the window is not yet known to be valid)."""
        return self.identifiable and self.controllable and self.window == "UNVERIFIED"

    @property
    def ready(self) -> bool:
        return self.identifiable and self.controllable and self.window == "OK"

    def to_dict(self) -> dict[str, Any]:
        return {"cluster": self.cluster, "identifiable": self.identifiable, "controllable": self.controllable,
                "window": self.window, "windowVerified": self.window_verified, "ready": self.ready, "smokeOnly": self.smoke_only,
                "workers": [w.to_dict() for w in self.workers]}


def assess_workers(docker: DockerCli, cluster: str, workers: Sequence[str], *, window_verified: bool = False) -> FaultReadiness:
    results = []
    for name in workers:
        info = docker.inspect(name)
        if info is None:
            results.append(WorkerReadiness(name, False, False, None, "UNREADABLE", "the container could not be inspected"))
            continue
        labels = info["labels"]
        identifiable = labels.get(KIND_CLUSTER_LABEL) == cluster and labels.get(KIND_ROLE_LABEL) == "worker"
        policy = docker.restart_policy(name)
        if policy is None:
            window, problem = "UNREADABLE", "the restart policy could not be read"
        elif policy in POLICY_OK:
            window, problem = "OK", None
        else:
            window, problem = "UNVERIFIED", (f"Docker restart policy is {policy!r}: the container may be restarted after it is killed, "
                                             "so the failure window must be observed once before it is trusted")
        results.append(WorkerReadiness(name, identifiable, bool(info["running"]), policy, window,
                                       problem if identifiable else "not a worker of this kind cluster"))
    return FaultReadiness(cluster, tuple(results), window_verified)
