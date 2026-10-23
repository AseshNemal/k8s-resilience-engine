from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.recommendation_evaluation_validation.application.models import ACTIVE, AppliedChange
from app.shared.persistence.models import AppliedChangeRecord

_FIELDS = ("application_id", "recommendation_id", "approval_id", "tier", "namespace", "status", "yaml_applied", "yaml_sha256",
           "before", "cluster", "events", "detail", "applied_by", "applied_at", "reset_by", "reset_at")


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

    def latest_for(self, recommendation_id: str) -> AppliedChange | None:
        row = self.session.scalar(select(AppliedChangeRecord).where(AppliedChangeRecord.recommendation_id == recommendation_id)
                                  .order_by(AppliedChangeRecord.id.desc()))
        return None if row is None else self._domain(row)

    def active(self) -> list[AppliedChange]:
        rows = self.session.scalars(select(AppliedChangeRecord).where(AppliedChangeRecord.status.in_(sorted(ACTIVE)))
                                    .order_by(AppliedChangeRecord.id))
        return [self._domain(row) for row in rows]
