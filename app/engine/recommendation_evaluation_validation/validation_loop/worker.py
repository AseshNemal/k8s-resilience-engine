"""Running a validation loop in the background, and recovering one that was cut off.

A run takes many minutes and a node failure blocks for several of them, so the loop runs on its own daemon thread, started on
demand, with every step written to the database as it happens. A page only ever reads that state, so closing the extension or
reloading the page never affects a run.

Recovery is NOT resumption. If the backend dies in the middle of a run, the next start finds a run that is still marked running
with nobody working on it. ``recover_run`` then does exactly two things: start the worker containers that were stopped, and put
back the Deployments from the undo information stored before the first write. It records the outcome in the database, and until
that outcome is recorded no other run may start. It never stops a node and never repeats an experiment; the person starts a new
run if they want one. A recovery step that does not complete leaves the run NEEDS_MANUAL_RESTORE with what to do by hand.
"""

import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from app.engine.recommendation_evaluation_validation.validation.models import ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation_loop.loop import (
    LoopObserver,
    LoopResult,
    ValidationLoop,
)
from app.engine.recommendation_evaluation_validation.validation_loop.models import (
    ExperimentKey,
    ExperimentOutcome,
    RunStatus,
)
from app.engine.recommendation_evaluation_validation.validation_loop.repository import LoopRepository


class Recoverer(Protocol):
    """What recovery needs. It can restore and revert; it has no way to stop a node or write a candidate."""

    def restore_workers(self) -> list[dict[str, Any]]:
        """Start every worker container this system stopped and has not restored. Each entry: container, restored, error."""

    def revert_workloads(self, restore_points: dict[str, Any] | None) -> dict[str, Any]:
        """Put back the Deployments from the stored undo information. ``{"reverted": True|False|None, "error": ...}``."""


class DbObserver(LoopObserver):
    """Writes everything the loop does to the run's rows, so the page can show it and a restart cannot lose it."""

    def __init__(self, repository: LoopRepository, run_key: str, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._repo, self._run, self._clock = repository, run_key, clock

    def run_started(self, plan, candidates) -> None:
        self._repo.add_event(self._run, "RUN_STARTED", f"{plan.mode.value}: {len(candidates)} candidate(s), {len(plan.workers)} worker(s)")

    def experiment_planned(self, key: ExperimentKey) -> None:
        self._repo.plan_experiment(self._run, key)

    def stage(self, key: ExperimentKey | None, stage: str, detail: str = "") -> None:
        current = {"stage": stage, "detail": detail, "at": self._clock().isoformat()}
        if key is not None:
            current.update({"kind": key.kind, "candidateId": key.candidate_id, "worker": key.worker, "repeat": key.repeat})
            if stage == "INJECT_FAILURE":
                self._repo.start_experiment(self._run, key)
        elif detail:
            current["candidateId"] = detail.replace("candidate ", "")
        self._repo.set_current(self._run, current)

    def experiment_finished(self, outcome: ExperimentOutcome) -> None:
        self._repo.finish_experiment(self._run, outcome)

    def candidate_finished(self, outcome) -> None:
        self._repo.add_event(self._run, "CANDIDATE_" + outcome.status.value, f"{outcome.candidate_id}: {outcome.reason or 'ok'}")


class LoopWorker:
    """Owns at most one running loop. State lives in the database; the thread is only the thing that is working."""

    def __init__(self, repository: LoopRepository) -> None:
        self._repo = repository
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._active: str | None = None
        self._reconciled = False

    # -- starting a run -----------------------------------------------------------------------------------------

    def is_working(self, run_key: str | None = None) -> bool:
        alive = self._thread is not None and self._thread.is_alive()
        return alive and (run_key is None or run_key == self._active)

    def start(self, run_key: str, build_loop: Callable[[LoopObserver, Callable[[], bool]], ValidationLoop],
              recoverer: Recoverer | None = None) -> None:
        """Start the planned run on its own thread. Refused while another run is going or still needs a person."""
        with self._lock:
            if self.is_working():
                raise ValidationRefusedError("RUN_ACTIVE", "A validation run is already in progress.")
            blocking = self._repo.blocking_run()
            if blocking is not None and not (blocking["runKey"] == run_key and blocking["status"] == RunStatus.PLANNED.value):
                raise ValidationRefusedError("RUN_BLOCKED", f"Run {blocking['runKey']} is {blocking['status']}; it has to be "
                                             "finished or recovered before another run starts.")
            self._repo.set_status(run_key, RunStatus.RUNNING)
            self._active = run_key
            self._thread = threading.Thread(target=self._execute, args=(run_key, build_loop, recoverer), daemon=True,
                                            name=f"validation-loop-{run_key}")
            self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _execute(self, run_key: str, build_loop, recoverer: Recoverer | None) -> None:
        observer = DbObserver(self._repo, run_key)
        try:
            loop = build_loop(observer, lambda: self._repo.abort_requested(run_key))
            result = loop.run()
            self._finish(run_key, result)
        except Exception as error:      # noqa: BLE001  (an unexpected stop: recover what was changed, then say so)
            self._repo.add_event(run_key, "RUN_CRASHED", f"{type(error).__name__}: {error}")
            self._repo.close_open_experiments(run_key, f"The run stopped unexpectedly: {error}")
            if recoverer is not None:
                self.recover_run(run_key, recoverer)
            else:
                self._repo.set_status(run_key, RunStatus.NEEDS_MANUAL_RESTORE, stop_reason=f"The run stopped unexpectedly: {error}")
        finally:
            self._active = None

    def _finish(self, run_key: str, result: LoopResult) -> None:
        self._repo.save_result(run_key, result.to_dict())
        for event in result.events:
            self._repo.add_event(run_key, event["event"], event["detail"])
        self._repo.close_open_experiments(run_key, result.stop_reason or "The run ended before this experiment ran.")
        if not result.needs_manual_restore:
            self._repo.clear_restore_points(run_key)
        self._repo.set_status(run_key, result.status, stop_reason=result.stop_reason)

    # -- abort and recovery -------------------------------------------------------------------------------------

    def abort(self, run_key: str) -> bool:
        """Ask a running run to stop at its next safe point (the worker is restarted and the Deployments are put back first)."""
        return self._repo.request_abort(run_key)

    def recover_run(self, run_key: str, recoverer: Recoverer) -> dict[str, Any]:
        """Restore workers and revert workloads of an interrupted run, and record the outcome. Never stops a node, never resumes."""
        run = self._repo.get_run(run_key)
        workers: list[dict[str, Any]] = []
        workloads: dict[str, Any] = {"reverted": None}
        try:
            workers = recoverer.restore_workers()
        except Exception as error:      # noqa: BLE001
            workers = [{"container": None, "restored": False, "error": str(error)}]
        try:
            workloads = recoverer.revert_workloads(run.get("restorePoints"))
        except Exception as error:      # noqa: BLE001
            workloads = {"reverted": False, "error": str(error)}
        done = all(entry.get("restored") for entry in workers) and workloads.get("reverted") is not False
        recovery = {"at": datetime.now(timezone.utc).isoformat(), "workers": workers, "workloads": workloads,
                    "complete": done, "nodesStopped": 0,
                    "note": "Recovery only restores and reverts. It does not repeat any experiment or stop any node."}
        self._repo.close_open_experiments(run_key, "The run was interrupted before this experiment finished.")
        self._repo.save_recovery(run_key, recovery)
        if done:
            self._repo.clear_restore_points(run_key)
            self._repo.add_event(run_key, "RECOVERED", "Workers restored and workloads reverted.")
            self._repo.set_status(run_key, RunStatus.INTERRUPTED, stop_reason="The run was interrupted and has been recovered.")
        else:
            self._repo.add_event(run_key, "RECOVERY_INCOMPLETE", "Something could not be restored; it needs a person.")
            self._repo.set_status(run_key, RunStatus.NEEDS_MANUAL_RESTORE,
                                  stop_reason="Recovery did not complete; restore the listed items by hand.")
        return recovery

    def reconcile(self, recoverers: Callable[[dict[str, Any]], Recoverer]) -> list[str]:
        """On a fresh backend: recover every run that is marked live while nothing is working on it. Once per process.

        ``recoverers`` builds the recoverer for a run from its stored record (its cluster and workers), because what has to be
        restored depends on which cluster that run was stopping nodes of.
        """
        recovered: list[str] = []
        with self._lock:
            if self._reconciled:
                return recovered
            self._reconciled = True
        for run in self._repo.live_runs():
            if self.is_working(run["runKey"]):
                continue
            self._repo.add_event(run["runKey"], "RECONCILE", "The backend restarted while this run was live; recovering only.")
            self.recover_run(run["runKey"], recoverers(run))
            recovered.append(run["runKey"])
        return recovered
