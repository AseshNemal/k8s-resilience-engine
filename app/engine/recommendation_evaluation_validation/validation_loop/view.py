"""The page's picture of a run, built only from what the database holds. Pure.

The rules this module enforces on what the page may say:

* Progress is a count of experiments that actually reached a state. It is never a percentage of time and never an estimate.
* A time estimate exists only when past experiments of the same kind have actually run (``observed_estimate``), and carries the
  number of experiments it rests on. With none, there is no estimate.
* Recovery and restoration are claimed only when evidence confirms them: a worker is "restored" when its experiment recorded a
  verified recovery, a Deployment is "restored" when its candidate recorded a verified revert. Until then the page says "not yet
  confirmed". While an abort is in progress the page says so, and keeps saying so until the run has ended.
* A run that stopped updating (no heartbeat for a while while it claims to be running) is shown as STALE, not as running.
* A model mismatch is part of the picture, always, in both views.
* The winner is the run's own selected candidate, which exists only if every planned experiment of it passed.
"""

from datetime import datetime, timezone
from typing import Any

from app.engine.recommendation_evaluation_validation.validation_loop.loop import STAGES, observed_estimate

STALE_AFTER_S = 180.0
RUNNING_STATES = ("RUNNING", "ABORTING")
FINISHED = ("PASS", "FAIL", "INCONCLUSIVE")
STAGE_LABELS = {"PREPARE": "Prepare", "APPLY_CANDIDATE": "Apply candidate", "VERIFY_ROLLOUT": "Verify rollout",
                "INJECT_FAILURE": "Inject failure", "PROBE": "Probe", "RECOVER_WORKER": "Recover worker",
                "RESTORE_DEPLOYMENT": "Restore deployment"}
STATUS_LABELS = {"PLANNED": "Planned", "RUNNING": "Running", "ABORTING": "Abort requested — recovering worker",
                 "COMPLETED": "Completed", "INTERRUPTED": "Stopped early", "FAILED": "Failed",
                 "NEEDS_MANUAL_RESTORE": "Needs your attention"}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _cell_status(repeats: list[dict[str, Any]]) -> str:
    states = [r["status"] for r in repeats]
    if "FAIL" in states:
        return "FAIL"
    if "RUNNING" in states:
        return "RUNNING"
    if states and all(s == "PASS" for s in states):
        return "PASS"
    if "INCONCLUSIVE" in states:
        return "INCONCLUSIVE"
    if states and all(s == "SKIPPED" for s in states):
        return "SKIPPED"
    return "PENDING"


def _checks(experiments: list[dict[str, Any]]) -> dict[str, int] | None:
    """How many of the workflow check's requests passed while the worker was down, over the finished repeats of one cell. None without a check."""
    passed = total = 0
    seen = False
    for e in experiments:
        window = (((e.get("outcome") or {}).get("probe") or {}).get("windows") or {}).get("failure")
        if window:
            seen = True
            passed += int(window.get("successes") or 0)
            total += int(window.get("samples") or 0)
    return {"passed": passed, "total": total} if seen else None


def _matrix(plan: dict[str, Any], experiments: list[dict[str, Any]]) -> dict[str, Any]:
    mode = plan.get("mode") or {}
    workers, repeats = list(mode.get("workers") or []), int(mode.get("repeats") or 1)
    rows = []
    for candidate in plan.get("candidates") or []:
        cells = []
        for worker in workers:
            found = {e["repeat"]: e for e in experiments if e["kind"] == "candidate" and e["candidateId"] == candidate["id"]
                     and e["worker"] == worker}
            reps = [{"repeat": n, "status": found[n]["status"] if n in found else "PENDING"} for n in range(1, repeats + 1)]
            cells.append({"worker": worker, "repeats": reps, "status": _cell_status(reps), "checks": _checks(list(found.values()))})
        rows.append({"candidateId": candidate["id"], "strategy": candidate.get("strategy"), "service": candidate.get("service"),
                     "cells": cells, "status": _cell_status([r for c in cells for r in c["repeats"]])})
    return {"workers": workers, "repeatsPerCell": repeats, "rows": rows}


def _metrics(plan: dict[str, Any], experiments: list[dict[str, Any]], run: dict[str, Any]) -> dict[str, Any]:
    mode = plan.get("mode") or {}
    workers, repeats = mode.get("workers") or [], int(mode.get("repeats") or 1)
    planned = len(plan.get("candidates") or []) * len(workers) * repeats + (len(workers) if mode.get("measureBaseline") else 0)
    counts: dict[str, int] = {}
    for e in experiments:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    done = sum(counts.get(s, 0) for s in FINISHED)
    tested = {e["candidateId"] for e in experiments if e["kind"] == "candidate" and e["status"] in FINISHED}
    covered = {e["worker"] for e in experiments if e["status"] in FINISHED}
    approval = run.get("approval") or {}
    return {"plannedExperiments": planned, "finished": done, "skipped": counts.get("SKIPPED", 0), "running": counts.get("RUNNING", 0),
            "candidatesTested": len(tested), "candidatesPlanned": len(plan.get("candidates") or []),
            "workersCovered": len(covered), "workersPlanned": len(workers),
            "killsUsed": run.get("killsUsed", 0), "maxFailures": approval.get("maxFailures"),
            "progress": {"done": done + counts.get("SKIPPED", 0), "total": planned}}


def _safety(run: dict[str, Any], experiments: list[dict[str, Any]]) -> dict[str, Any]:
    status = run["status"]
    recoveries = [(e["outcome"] or {}).get("recovery") for e in experiments if (e["outcome"] or {}).get("recovery")]
    workers_ok = bool(recoveries) and all(r.get("verified") is True for r in recoveries)
    candidates = ((run.get("result") or {}).get("candidates") or [])
    reverted = [c.get("reverted") for c in candidates if c.get("reverted") is not None]
    deployments_ok = bool(reverted) and all(v is True for v in reverted)
    recovery = run.get("recovery")
    finished = [e for e in experiments if e["kind"] == "candidate" and e["status"] in FINISHED]
    judged = [(e["outcome"] or {}).get("modelAgrees") for e in finished if (e["outcome"] or {}).get("modelAgrees") is not None]
    blocked = any(str(r).startswith("Blocked before the kill") for e in experiments for r in ((e["outcome"] or {}).get("reasons") or []))
    body: dict[str, Any] = {
        # What the run's own guard found before each stop, and whether the model agreed with what was observed. Both are read from
        # the record: "passed" means no stop was refused by the guard, not that nothing could go wrong.
        "criticalCheck": "blocked" if blocked else ("passed" if experiments else "pending"),
        "predictionCheck": "pending" if not judged else ("mismatch" if any(v is False for v in judged) else "matches"),
        "workersRestored": "confirmed" if workers_ok else ("none-stopped" if not recoveries else "not-confirmed"),
        "deploymentsRestored": "confirmed" if deployments_ok else ("none-changed" if not candidates else "not-confirmed"),
        "recovery": recovery,
    }
    if status == "NEEDS_MANUAL_RESTORE":
        body.update(level="alert", message="Something could not be restored. The cluster needs you before another run.",
                    actions=_manual_actions(run, recovery))
    elif status == "ABORTING":
        body.update(level="warning", message="Abort requested — recovering worker. Nothing is claimed restored until it is verified.")
    elif status in RUNNING_STATES:
        body.update(level="info", message="A worker is stopped on purpose during each test and restored after it.")
    elif status == "INTERRUPTED" and recovery and not recovery.get("complete"):
        body.update(level="alert", message="Recovery did not complete.", actions=_manual_actions(run, recovery))
    elif status in ("COMPLETED", "INTERRUPTED", "FAILED"):
        confirmed = body["workersRestored"] != "not-confirmed" and body["deploymentsRestored"] != "not-confirmed"
        body.update(level="ok" if confirmed else "warning",
                    message=("Workers and Deployments are confirmed restored." if confirmed else
                             "Restoration is not confirmed by the recorded evidence. Check the cluster."))
    else:
        body.update(level="info", message="Nothing has been changed on the cluster.")
    return body


def _manual_actions(run: dict[str, Any], recovery: dict[str, Any] | None) -> list[str]:
    actions: list[str] = []
    for worker in (recovery or {}).get("workers") or []:
        if not worker.get("restored") and worker.get("container"):
            actions.append(f"docker start {worker['container']}")
    if ((recovery or {}).get("workloads") or {}).get("reverted") is False:
        actions.append("Re-apply the original Deployments (the stored restore points are kept for a retry).")
    if not actions:
        actions.append("Use 'Try recovery again' once Docker and the cluster are reachable.")
    return actions


def _result(run: dict[str, Any], experiments: list[dict[str, Any]]) -> dict[str, Any] | None:
    result = run.get("result")
    if not result:
        return None
    candidates = result.get("candidates") or []
    winner = result.get("winner")
    mismatches = [{"candidateId": e["candidateId"], "worker": e["worker"], "repeat": e["repeat"],
                   "predictedSurvives": ((e["outcome"] or {}).get("predicted") or {}).get("workflowSurvives"),
                   "readinessHolds": ((e["outcome"] or {}).get("readiness") or {}).get("holds")}
                  for e in experiments if e["kind"] == "candidate" and (e["outcome"] or {}).get("modelAgrees") is False]
    probe_findings = [{"candidateId": e["candidateId"], "worker": e["worker"], "repeat": e["repeat"]}
                      for e in experiments if (e["outcome"] or {}).get("probeDisagreesWithReadiness") is True]
    improvement = None
    if winner:
        comparisons = [(e["outcome"] or {}).get("comparison", {}).get("blastRadius") for e in experiments
                       if e["kind"] == "candidate" and e["candidateId"] == winner and e["status"] == "PASS"]
        comparisons = [c for c in comparisons if c]
        if comparisons:
            improvement = {"worstObservedBlastRadiusAfter": max(c["observedAfter"] for c in comparisons),
                           "observedBefore": max(c["observedBefore"] for c in comparisons),
                           "baselineBasis": comparisons[0].get("baselineBasis"),
                           "basisNote": ("Measured by stopping each worker before the change." if comparisons[0].get("baselineBasis") == "MEASURED"
                                         else "The before-value is derived from the placement, not measured.")}
    return {"winner": winner, "stopReason": result.get("stopReason"), "killsUsed": result.get("killsUsed"),
            "candidates": [{"candidateId": c["candidateId"], "status": c["status"], "reason": c.get("reason"),
                            "reverted": c.get("reverted")} for c in candidates],
            "improvement": improvement, "modelMismatches": mismatches, "probeFindings": probe_findings}


def _timing(run: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Seconds since the run started, and its length once it has ended. From the stored timestamps only."""
    started, finished = _parse(run.get("startedAt")), _parse(run.get("finishedAt"))
    if started is None:
        return {"elapsedS": None, "startedAgoS": None}
    end = finished or now
    return {"elapsedS": max(0.0, round((end - started).total_seconds(), 1)), "startedAgoS": max(0.0, round((now - started).total_seconds(), 1))}


def build_view(run: dict[str, Any], experiments: list[dict[str, Any]], durations: list[float], *, now: datetime | None = None,
               worker_alive: bool | None = None, research: bool = False) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    plan = run.get("plan") or {}
    status = run["status"]
    heartbeat = _parse(run.get("heartbeatAt")) or _parse(run.get("startedAt"))
    age = (now - heartbeat).total_seconds() if heartbeat and status in RUNNING_STATES else None
    stale = bool(status in RUNNING_STATES and worker_alive is False and age is not None and age > STALE_AFTER_S)
    metrics = _metrics(plan, experiments, run)
    remaining = max(metrics["plannedExperiments"] - metrics["progress"]["done"], 0)
    mode = plan.get("mode") or {}
    current = run.get("current")
    view: dict[str, Any] = {
        "runKey": run["runKey"], "status": status, "statusLabel": "No update from the run — it may have stopped" if stale else STATUS_LABELS.get(status, status),
        "stale": stale, "heartbeatAgeS": None if age is None else round(age, 1),
        "target": {"kind": run["targetKind"], "name": run["targetName"], "clusterUid": run["clusterUid"]},
        "application": {"namespace": run["namespace"], "workflowId": run["workflowId"],
                        "workflowBasis": plan.get("workflowBasis")},
        "mode": mode.get("mode"), "preliminary": bool(mode.get("preliminary")),
        "metrics": metrics, "matrix": _matrix(plan, experiments),
        "baseline": [{"worker": e["worker"], "status": e["status"]} for e in experiments if e["kind"] == "baseline"],
        "current": current if status in RUNNING_STATES else None,
        "stages": [{"id": s, "label": STAGE_LABELS[s], "active": bool(current and current.get("stage") == s and status in RUNNING_STATES)}
                   for s in STAGES],
        "abort": {"available": status == "RUNNING", "requested": bool(run.get("abortRequested")),
                  "label": "Abort requested — recovering worker" if status == "ABORTING" else None},
        "safety": _safety(run, experiments), "result": _result(run, experiments), "stopReason": run.get("stopReason"),
        "estimate": observed_estimate(durations, remaining) if status in RUNNING_STATES else None,
        "startedAt": run.get("startedAt"), "finishedAt": run.get("finishedAt"), "timing": _timing(run, now),
        "baselineMeasured": bool(mode.get("measureBaseline")),
        "evidenceLevel": "L2_PROBE" if plan.get("probe") else "L1_READINESS",
        "canStartAnother": status in ("COMPLETED", "INTERRUPTED", "FAILED") and not (run.get("recovery") and not run["recovery"].get("complete")),
    }
    if research:
        view["research"] = {"experiments": experiments, "events": run.get("events"), "approval": run.get("approval"),
                            "plan": plan, "recovery": run.get("recovery")}
    return view
