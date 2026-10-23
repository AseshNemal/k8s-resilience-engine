from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.recommendation_evaluation_validation.application.models import ACTIVE, AppliedChange
from app.shared.persistence.models import AppliedChangeRecord

_FIELDS = ("application_id", "recommendation_id", "approval_id", "tier", "namespace", "status", "yaml_applied", "yaml_sha256",
           "before", "cluster", "events", "detail", "rollout", "applied_by", "applied_at", "reset_by", "reset_at")


class AppliedChangeRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: AppliedChangeRecord) -> AppliedChange:
        return AppliedChange(id=row.id, **{name: getattr(row, name) for name in _FIELDS})

    def save(self, change: AppliedChange) -> AppliedChange:
        row = AppliedChangeRecord(**{name: getattr(change, name) for name in _FIELDS})
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return self._domain(row)

    def update(self, change: AppliedChange) -> AppliedChange:
        row = self.session.scalar(select(AppliedChangeRecord).where(AppliedChangeRecord.application_id == change.application_id))
        if row is None:
            raise LookupError(change.application_id)
        for name in _FIELDS:
            setattr(row, name, getattr(change, name))
        self.session.commit()
        self.session.refresh(row)
        return self._domain(row)

    def _row(self, application_id: str) -> AppliedChangeRecord | None:
        row = self.session.scalar(select(AppliedChangeRecord).where(AppliedChangeRecord.application_id == application_id))
        if row is not None:
            self.session.refresh(row)       # never a copy this session loaded before another request wrote
        return row

    def get(self, application_id: str) -> AppliedChange | None:
        row = self._row(application_id)
        return None if row is None else self._domain(row)

    def latest_for(self, recommendation_id: str) -> AppliedChange | None:
        row = self.session.scalar(select(AppliedChangeRecord).where(AppliedChangeRecord.recommendation_id == recommendation_id)
                                  .order_by(AppliedChangeRecord.id.desc()))
        return None if row is None else self._domain(row)

    def latest(self) -> AppliedChange | None:
        """The most recent change of any recommendation: the one whose state the cluster is in."""
        row = self.session.scalar(select(AppliedChangeRecord).order_by(AppliedChangeRecord.id.desc()))
        return None if row is None else self._domain(row)

    def recent(self, limit: int = 20) -> list[AppliedChange]:
        rows = self.session.scalars(select(AppliedChangeRecord).order_by(AppliedChangeRecord.id.desc()).limit(limit))
        return [self._domain(row) for row in rows]

    def active(self) -> list[AppliedChange]:
        rows = self.session.scalars(select(AppliedChangeRecord).where(AppliedChangeRecord.status.in_(sorted(ACTIVE)))
                                    .order_by(AppliedChangeRecord.id))
        return [self._domain(row) for row in rows]

    # -- targeted writes ---------------------------------------------------------------------------------------------
    # A status poll must never write back a whole stale copy of a change another request has since applied or reset, so
    # what it records goes through these: the rollout (only if it is still the one the poll read) and appended events.

    def advance_rollout(self, application_id: str, expected_since: str | None, rollout: dict[str, Any],
                        events: list[dict[str, Any]]) -> AppliedChange | None:
        """Store ``rollout`` and append ``events`` only if the stored rollout is still the one that began at ``expected_since``.

        Returns the stored change, or None when it was superseded (a reset began since) and nothing was written.
        """
        row = self._row(application_id)
        if row is None or (row.rollout or {}).get("since") != expected_since:
            return None
        row.rollout = rollout
        if events:
            row.events = [*row.events, *events]
        self.session.commit()
        self.session.refresh(row)
        return self._domain(row)

    def append_events(self, application_id: str, events: list[dict[str, Any]]) -> None:
        row = self._row(application_id)
        if row is None or not events:
            return
        row.events = [*row.events, *events]
        self.session.commit()
