"""Storage of experiment records. Append-only in spirit: the prediction is written once and an ended experiment is final."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.recommendation_evaluation_validation.validation.cluster_gateway import REAL_CLUSTER
from app.engine.recommendation_evaluation_validation.validation.experiment_record import ExperimentRecord
from app.engine.recommendation_evaluation_validation.validation.models import RunState
from app.shared.persistence.models import ValidationRunRecord

# Written when the experiment is planned and never again.
IMMUTABLE = ("recommendation_id", "approval_id", "workflow_id", "evidence_class", "cluster", "workflow", "before_evidence",
             "recommendation", "proposal", "predicted")
# Written once, when the experiment ends.
RESULT_FIELDS = ("status", "validated", "is_measurement", "observed", "comparison", "prediction_error", "failure",
                 "events", "provenance", "started_at", "finished_at")


class ExperimentExistsError(Exception):
    """An experiment with this id is already stored."""


class ExperimentAlreadyFinalizedError(Exception):
    """The experiment has ended; its outcome is not rewritten."""


class PredictionAlteredError(Exception):
    """A record to be finalized does not carry the prediction (or plan) that was stored when it was planned."""


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class ExperimentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _row(self, experiment_id: str) -> ValidationRunRecord:
        row = self.session.scalar(select(ValidationRunRecord).where(ValidationRunRecord.experiment_id == experiment_id))
        if row is None:
            raise KeyError(f"No experiment {experiment_id!r}")
        return row

    def create(self, record: ExperimentRecord) -> None:
        if self.session.scalar(select(ValidationRunRecord.id).where(
                ValidationRunRecord.experiment_id == record.experiment_id)) is not None:
            raise ExperimentExistsError(record.experiment_id)
        self.session.add(ValidationRunRecord(**{**record.__dict__}))
        self.session.commit()

    def finalize(self, record: ExperimentRecord) -> None:
        row = self._row(record.experiment_id)
        if row.finished_at is not None:
            raise ExperimentAlreadyFinalizedError(record.experiment_id)
        changed = [name for name in IMMUTABLE if getattr(row, name) != getattr(record, name)]
        if changed:
            raise PredictionAlteredError(f"{record.experiment_id}: {', '.join(changed)} differ from what was planned")
        for name in RESULT_FIELDS:
            setattr(row, name, getattr(record, name))
        self.session.commit()

    def get(self, experiment_id: str) -> ExperimentRecord:
        return self._to_record(self._row(experiment_id))

    def list(self) -> list[ExperimentRecord]:
        rows = self.session.scalars(select(ValidationRunRecord).order_by(ValidationRunRecord.id.desc()))
        return [self._to_record(row) for row in rows]

    def list_measurements(self, workflow_basis: str | None = None) -> list[ExperimentRecord]:
        """The only records that may be shown as experimental results: completed runs on a real cluster.

        ``workflow_basis`` narrows them to FROZEN (an observed, approved workflow) or DECLARED (a person's statement, so the result is
        conditional on it); a record made before the field existed counts as FROZEN. Never mix the two in one claim without saying so.
        """
        rows = self.session.scalars(select(ValidationRunRecord).where(
            ValidationRunRecord.status == RunState.COMPLETED.value, ValidationRunRecord.is_measurement.is_(True),
            ValidationRunRecord.evidence_class == REAL_CLUSTER).order_by(ValidationRunRecord.id.desc()))
        records = [self._to_record(row) for row in rows]
        if workflow_basis is None:
            return records
        return [r for r in records if (r.provenance.get("workflowBasis") or "FROZEN") == workflow_basis]

    @staticmethod
    def _to_record(row: ValidationRunRecord) -> ExperimentRecord:
        return ExperimentRecord(
            experiment_id=row.experiment_id, recommendation_id=row.recommendation_id, approval_id=row.approval_id,
            workflow_id=row.workflow_id, status=row.status, validated=row.validated, is_measurement=row.is_measurement,
            evidence_class=row.evidence_class, cluster=row.cluster, workflow=row.workflow, before_evidence=row.before_evidence,
            recommendation=row.recommendation, proposal=row.proposal, predicted=row.predicted, observed=row.observed,
            comparison=row.comparison, prediction_error=row.prediction_error, failure=row.failure, events=row.events,
            provenance=row.provenance, created_at=_utc(row.created_at), started_at=_utc(row.started_at),
            finished_at=_utc(row.finished_at))
