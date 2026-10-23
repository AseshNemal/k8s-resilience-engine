"""Storage of validation-loop runs and their experiments, and the durable kill budget.

Every method opens its own short transaction through a session factory, because the run's worker thread and the page's requests
write to the same rows. Nothing here decides anything: it records what the loop did, in the order it happened.

The kill budget is one atomic UPDATE (``kills_used = kills_used + n`` only while the result stays within the grant), so two
writers cannot both spend the last failure and a restart cannot reset the count.
"""

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.engine.recommendation_evaluation_validation.validation_loop.grant import RunGrant
from app.engine.recommendation_evaluation_validation.validation_loop.models import (
    TERMINAL_RUN_STATUSES,
    ExperimentKey,
    ExperimentOutcome,
    ExperimentStatus,
    RunStatus,
)
from app.shared.persistence.models import ValidationLoopExperimentRecord, ValidationLoopRunRecord

# A run in one of these states blocks starting another: it is running, or it left the cluster needing a person.
BLOCKING = (RunStatus.PLANNED.value, RunStatus.RUNNING.value, RunStatus.ABORTING.value, RunStatus.NEEDS_MANUAL_RESTORE.value)
LIVE = (RunStatus.RUNNING.value, RunStatus.ABORTING.value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _iso(value: datetime | None) -> str | None:
    value = _utc(value)
    return None if value is None else value.isoformat()


class RunNotFoundError(KeyError):
    pass


class LoopRepository:
    def __init__(self, session_factory: Callable[[], Session], clock: Callable[[], datetime] = _now) -> None:
        self._factory, self._clock = session_factory, clock

    @contextmanager
    def _session(self):
        session = self._factory()
        try:
            yield session
        finally:
            session.close()

    def _run(self, session: Session, run_key: str) -> ValidationLoopRunRecord:
        row = session.scalar(select(ValidationLoopRunRecord).where(ValidationLoopRunRecord.run_key == run_key))
        if row is None:
            raise RunNotFoundError(run_key)
        return row

    # -- runs ---------------------------------------------------------------------------------------------------

    def create_run(self, grant: RunGrant, *, mode: str, target_kind: str, workflow_id: str, plan: dict[str, Any]) -> None:
        with self._session() as session:
            session.add(ValidationLoopRunRecord(
                run_key=grant.run_id, status=RunStatus.PLANNED.value, mode=mode, target_kind=target_kind,
                target_name=grant.target_name, cluster_uid=grant.cluster_uid, namespace=grant.namespace, workflow_id=workflow_id,
                approval=grant.to_dict(), plan=plan, kills_used=0, abort_requested=False, events=[], created_at=self._clock()))
            session.commit()

    def get_run(self, run_key: str) -> dict[str, Any]:
        with self._session() as session:
            return self._run_dict(self._run(session, run_key))

    def latest_run(self) -> dict[str, Any] | None:
        with self._session() as session:
            row = session.scalar(select(ValidationLoopRunRecord).order_by(ValidationLoopRunRecord.id.desc()).limit(1))
            return None if row is None else self._run_dict(row)

    def blocking_run(self) -> dict[str, Any] | None:
        """A run that is not finished, or that finished needing a person: another run may not start while it exists."""
        with self._session() as session:
            row = session.scalar(select(ValidationLoopRunRecord).where(ValidationLoopRunRecord.status.in_(BLOCKING))
                                 .order_by(ValidationLoopRunRecord.id.desc()).limit(1))
            return None if row is None else self._run_dict(row)

    def live_runs(self) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.scalars(select(ValidationLoopRunRecord).where(ValidationLoopRunRecord.status.in_(LIVE))).all()
            return [self._run_dict(row) for row in rows]

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.scalars(select(ValidationLoopRunRecord).order_by(ValidationLoopRunRecord.id.desc()).limit(limit)).all()
            return [self._run_dict(row) for row in rows]

    def set_status(self, run_key: str, status: RunStatus, *, stop_reason: str | None = None) -> None:
        with self._session() as session:
            row = self._run(session, run_key)
            row.status = status.value
            if status is RunStatus.RUNNING and row.started_at is None:
                row.started_at = self._clock()
            if status in TERMINAL_RUN_STATUSES:
                row.finished_at, row.current = self._clock(), None
            if stop_reason is not None:
                row.stop_reason = stop_reason
            session.commit()

    def heartbeat(self, run_key: str) -> None:
        with self._session() as session:
            self._run(session, run_key).heartbeat_at = self._clock()
            session.commit()

    def request_abort(self, run_key: str) -> bool:
        """Ask the run to stop at its next safe point. False when it is not running."""
        with self._session() as session:
            row = self._run(session, run_key)
            if row.status not in LIVE:
                return False
            row.abort_requested, row.status = True, RunStatus.ABORTING.value
            self._event(row, "ABORT_REQUESTED", "Abort requested; the run stops at a safe point.")
            session.commit()
            return True

    def abort_requested(self, run_key: str) -> bool:
        with self._session() as session:
            return bool(self._run(session, run_key).abort_requested)

    def set_current(self, run_key: str, current: dict[str, Any] | None) -> None:
        with self._session() as session:
            row = self._run(session, run_key)
            row.current, row.heartbeat_at = current, self._clock()
            session.commit()

    def save_restore_points(self, run_key: str, points: dict[str, Any]) -> None:
        with self._session() as session:
            self._run(session, run_key).restore_points = points
            session.commit()

    def clear_restore_points(self, run_key: str) -> None:
        with self._session() as session:
            self._run(session, run_key).restore_points = None
            session.commit()

    def save_result(self, run_key: str, result: dict[str, Any]) -> None:
        with self._session() as session:
            row = self._run(session, run_key)
            row.result = result
            session.commit()

    def save_recovery(self, run_key: str, recovery: dict[str, Any]) -> None:
        with self._session() as session:
            self._run(session, run_key).recovery = recovery
            session.commit()

    def add_event(self, run_key: str, event: str, detail: str = "") -> None:
        with self._session() as session:
            row = self._run(session, run_key)
            self._event(row, event, detail)
            session.commit()

    def _event(self, row: ValidationLoopRunRecord, event: str, detail: str) -> None:
        row.events = list(row.events) + [{"at": self._clock().isoformat(), "event": event, "detail": detail}]

    # -- the kill budget ----------------------------------------------------------------------------------------

    def reserve(self, run_id: str, count: int, maximum: int) -> bool:
        if count < 1:
            return False
        with self._session() as session:
            outcome = session.execute(
                update(ValidationLoopRunRecord)
                .where(ValidationLoopRunRecord.run_key == run_id, ValidationLoopRunRecord.kills_used + count <= maximum)
                .values(kills_used=ValidationLoopRunRecord.kills_used + count))
            session.commit()
            return outcome.rowcount == 1

    def used(self, run_id: str) -> int:
        with self._session() as session:
            return int(self._run(session, run_id).kills_used)

    # -- experiments --------------------------------------------------------------------------------------------

    def plan_experiment(self, run_key: str, key: ExperimentKey) -> None:
        with self._session() as session:
            if self._experiment(session, run_key, key) is None:
                seq = len(session.scalars(select(ValidationLoopExperimentRecord.id).where(
                    ValidationLoopExperimentRecord.run_key == run_key)).all()) + 1
                session.add(ValidationLoopExperimentRecord(
                    run_key=run_key, seq=seq, kind=key.kind, candidate_id=key.candidate_id, worker=key.worker, repeat=key.repeat,
                    status=ExperimentStatus.PENDING.value))
                session.commit()

    def start_experiment(self, run_key: str, key: ExperimentKey) -> None:
        with self._session() as session:
            row = self._experiment(session, run_key, key)
            if row is not None:
                row.status, row.started_at = ExperimentStatus.RUNNING.value, self._clock()
                session.commit()

    def finish_experiment(self, run_key: str, outcome: ExperimentOutcome) -> None:
        self.plan_experiment(run_key, outcome.key)
        with self._session() as session:
            row = self._experiment(session, run_key, outcome.key)
            row.status, row.outcome, row.duration_s = outcome.status.value, outcome.to_dict(), outcome.duration_s
            row.experiment_id, row.finished_at = outcome.experiment_id, self._clock()
            session.commit()

    def close_open_experiments(self, run_key: str, reason: str) -> int:
        """Mark every experiment that never finished (a run that was cut off) so none stays RUNNING or PENDING for ever."""
        with self._session() as session:
            rows = session.scalars(select(ValidationLoopExperimentRecord).where(
                ValidationLoopExperimentRecord.run_key == run_key,
                ValidationLoopExperimentRecord.status.in_((ExperimentStatus.PENDING.value, ExperimentStatus.RUNNING.value)))).all()
            for row in rows:
                interrupted = row.status == ExperimentStatus.RUNNING.value
                row.status = ExperimentStatus.INCONCLUSIVE.value if interrupted else ExperimentStatus.SKIPPED.value
                row.outcome = {"status": row.status, "reasons": [reason], "kind": row.kind, "candidateId": row.candidate_id,
                               "worker": row.worker, "repeat": row.repeat}
                row.finished_at = self._clock()
            session.commit()
            return len(rows)

    def experiments(self, run_key: str) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.scalars(select(ValidationLoopExperimentRecord).where(
                ValidationLoopExperimentRecord.run_key == run_key).order_by(ValidationLoopExperimentRecord.seq)).all()
            return [{"seq": r.seq, "kind": r.kind, "candidateId": r.candidate_id, "worker": r.worker, "repeat": r.repeat,
                     "status": r.status, "experimentId": r.experiment_id, "outcome": r.outcome, "durationS": r.duration_s,
                     "startedAt": _iso(r.started_at), "finishedAt": _iso(r.finished_at)} for r in rows]

    def observed_durations(self, *, limit: int = 50) -> list[float]:
        """Durations of experiments that actually ran to a verdict (any past run), for estimates. Empty when none did."""
        with self._session() as session:
            query = select(ValidationLoopExperimentRecord.duration_s).where(
                ValidationLoopExperimentRecord.duration_s.is_not(None),
                ValidationLoopExperimentRecord.status.in_((ExperimentStatus.PASS.value, ExperimentStatus.FAIL.value)))
            return [float(v) for v in session.scalars(query.order_by(ValidationLoopExperimentRecord.id.desc()).limit(limit)).all()]

    def _experiment(self, session: Session, run_key: str, key: ExperimentKey) -> ValidationLoopExperimentRecord | None:
        return session.scalar(select(ValidationLoopExperimentRecord).where(
            ValidationLoopExperimentRecord.run_key == run_key, ValidationLoopExperimentRecord.kind == key.kind,
            ValidationLoopExperimentRecord.candidate_id == key.candidate_id, ValidationLoopExperimentRecord.worker == key.worker,
            ValidationLoopExperimentRecord.repeat == key.repeat))

    @staticmethod
    def _run_dict(row: ValidationLoopRunRecord) -> dict[str, Any]:
        return {"runKey": row.run_key, "status": row.status, "mode": row.mode, "targetKind": row.target_kind,
                "targetName": row.target_name, "clusterUid": row.cluster_uid, "namespace": row.namespace,
                "workflowId": row.workflow_id, "approval": row.approval, "plan": row.plan, "killsUsed": row.kills_used,
                "abortRequested": row.abort_requested, "current": row.current, "restorePoints": row.restore_points,
                "result": row.result, "recovery": row.recovery, "stopReason": row.stop_reason, "events": list(row.events),
                "createdAt": _iso(row.created_at), "startedAt": _iso(row.started_at), "finishedAt": _iso(row.finished_at),
                "heartbeatAt": _iso(row.heartbeat_at)}
