"""Watching the rollout an applied (or reset) change causes, from Deployment evidence alone.

A patch being accepted is not a rollout: the API server stores the desired state and the controller converges
towards it. This module reads the Deployments the change touched and decides, per Deployment and overall,
where the rollout stands:

* ``rolling-out``: the controller has not observed the latest generation yet, or the replica counts have not
  reached it (updated, ready and available all equal to the desired count). Old ready pods never count: a
  Deployment whose ``observedGeneration`` is behind is rolling out even when its old pods are Ready;
* ``completed``: every touched Deployment is settled on its latest generation;
* ``failed``: a touched Deployment is gone or replaced (another UID), or its controller reports it cannot progress
  (Progressing=False, for example ProgressDeadlineExceeded — new pods unschedulable or never ready);
* ``timed-out``: the deadline passed (``ROLLOUT_TIMEOUT`` after the write) without any of the above. Pods the scheduler
  reports as unschedulable are shown as ``blocked`` while the rollout is still waiting, not as a failure by themselves;
* ``unknown``: nothing observed yet (a change recorded before rollout tracking, or before the first read).

A lost connection is not a rollout state: the read is retried on the next status request, and the response
says ``connection-lost`` for that read only while the persisted state stays what it was.

The record is persisted on the AppliedChange, so a page refresh or a backend restart resumes watching the
same rollout instead of starting over. Only state transitions are added to the change's events, never one
event per poll.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol, Sequence

ROLLING_OUT = "rolling-out"
COMPLETED = "completed"
TIMED_OUT = "timed-out"
FAILED = "failed"
UNKNOWN = "unknown"
CONNECTION_LOST = "connection-lost"     # response-only: this read failed; the persisted state is unchanged

# Only a completed rollout stops being watched. A timed-out or failed one can still recover (pods become schedulable, an image
# finishes pulling), and a person re-checking it should see that, so it is read again on the next status request.
TERMINAL = frozenset({COMPLETED})

ROLLOUT_TIMEOUT = timedelta(minutes=10)

DIRECTION_APPLY = "apply"
DIRECTION_RESET = "reset"


class RolloutReader(Protocol):
    """The one read this module needs from the cluster (``KubernetesTestClusterGateway.read_rollout``)."""

    def read_rollout(self, namespace: str, names: Sequence[str]) -> Mapping[str, Any]: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fresh(direction: str, namespace: str, deployments: Sequence[str], now: datetime | None = None,
          timeout: timedelta = ROLLOUT_TIMEOUT, uids: Mapping[str, str | None] | None = None) -> dict[str, Any]:
    """The record written the moment a direction's patches were accepted: nothing is known to have rolled out yet.

    ``uids`` are the identities of the Deployments as they were read before the write; a Deployment later seen with another
    identity was deleted and recreated, and is reported failed rather than counted as the one that was changed.
    """
    start = now or _now()
    uids = uids or {}
    return {
        "direction": direction, "namespace": namespace, "state": ROLLING_OUT,
        "since": start.isoformat(), "deadline": (start + timeout).isoformat(), "checkedAt": None,
        "blocked": False, "blockedReason": None,
        "deployments": [{"name": name, "state": ROLLING_OUT, "uid": uids.get(name)} for name in deployments],
    }


def _deployment_state(name: str, rollout: Any, expected_uid: str | None) -> dict[str, Any]:
    """One Deployment's rollout from its status; ``rollout`` is a ``DeploymentRollout`` from the gateway."""
    entry = {
        "name": name,
        "uid": rollout.uid or expected_uid,
        "generation": rollout.generation,
        "observedGeneration": rollout.observed_generation,
        "desired": rollout.desired,
        "updated": rollout.updated,
        "total": getattr(rollout, "total", None),
        "ready": rollout.ready,
        "available": rollout.available,
        "blockedReason": getattr(rollout, "blocked_reason", None),
    }
    if rollout.missing:
        return {**entry, "state": FAILED, "reason": "The Deployment is no longer in the cluster."}
    if expected_uid and rollout.uid and rollout.uid != expected_uid:
        return {**entry, "uid": expected_uid, "state": FAILED,
                "reason": "The Deployment is not the one that was changed (it was deleted and recreated)."}
    if rollout.progress_failed_reason:
        return {**entry, "state": FAILED,
                "reason": f"The controller reports it cannot progress: {rollout.progress_failed_reason}."}
    observed = rollout.observed_generation or 0
    generation = rollout.generation or 0
    if observed < generation:
        return {**entry, "state": ROLLING_OUT,
                "reason": "The controller has not observed the latest change yet (generation "
                          f"{observed} of {generation})."}
    # ``total`` counts every pod the Deployment still owns: while it exceeds the desired count, old-generation pods are
    # still being replaced, and "ready" among them would be old pods, which prove nothing about the new generation.
    counts = {"updated": rollout.updated, "total": getattr(rollout, "total", rollout.desired),
              "ready": rollout.ready, "available": rollout.available}
    short = {key: value for key, value in counts.items() if value != rollout.desired}
    if short:
        detail = ", ".join(f"{key} {counts[key] or 0} of {rollout.desired}" for key in ("updated", "total", "ready", "available"))
        return {**entry, "state": ROLLING_OUT, "reason": f"Waiting for replicas: {detail}."}
    return {**entry, "state": "settled", "reason": None}


def observe(record: dict[str, Any] | None, reader: RolloutReader, *, now: datetime | None = None,
            timeout: timedelta = ROLLOUT_TIMEOUT) -> tuple[dict[str, Any], str | None]:
    """Read the touched Deployments and advance the persisted record.

    Returns the new record and the transition event to log (None when the state did not change). A record in a
    terminal state is returned unchanged (it is read back, not re-watched). The caller persists the record.
    """
    if record is None:      # applied before rollout tracking existed: reported as unknown, never guessed
        return {"direction": None, "state": UNKNOWN, "since": None, "deadline": None, "checkedAt": None,
                "deployments": []}, None
    if record.get("state") in TERMINAL:
        return record, None
    moment = now or _now()
    expected = {d["name"]: d.get("uid") for d in record.get("deployments") or []}
    read = reader.read_rollout(record.get("namespace") or "", list(expected))
    deployments = [_deployment_state(name, read[name], expected[name]) for name in expected if name in read]
    deadline = record.get("deadline")
    past_deadline = deadline is not None and moment > datetime.fromisoformat(deadline)
    if deployments and len(deployments) == len(expected) and all(d["state"] == "settled" for d in deployments):
        state = COMPLETED
    elif any(d["state"] == FAILED for d in deployments):
        state = FAILED
    elif past_deadline:
        state = TIMED_OUT
    else:
        state = ROLLING_OUT
    blocked = [d["blockedReason"] for d in deployments if d.get("blockedReason") and d["state"] != "settled"]
    previous = record.get("state")
    updated = {**record, "state": state, "checkedAt": moment.isoformat(), "deployments": deployments,
               "blocked": bool(blocked) and state != COMPLETED, "blockedReason": blocked[0] if blocked and state != COMPLETED else None}
    if state == COMPLETED:
        updated["completedAt"] = moment.isoformat()
    event = None if state == previous else f"rollout-{state}"
    return updated, event
