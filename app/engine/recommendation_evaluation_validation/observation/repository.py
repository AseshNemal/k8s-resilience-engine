from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence.models import Base


def now():
    return datetime.now(timezone.utc)


class FailureObservation(Base):
    __tablename__ = "failure_observations"
    observation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    # 1 while the observation is still watching; NULL once it ended. Unique, so two watches can never be active together.
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(30))
    namespace: Mapped[str] = mapped_column(String(253))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ObservationAlreadyActive(Exception):
    pass


class ObservationRepository:
    """Each call owns its session; the record is the only state, so a restart loses nothing."""

    def __init__(self, engine):
        self.engine = engine

    def create(self, observation_id, namespace, payload, status):
        with Session(self.engine) as db:
            db.add(FailureObservation(observation_id=observation_id, active_slot=1, status=status,
                                      namespace=namespace, payload=payload))
            try:
                db.commit()
            except IntegrityError as error:
                db.rollback()
                raise ObservationAlreadyActive() from error

    @staticmethod
    def _view(row):
        return {**row.payload, "observationId": row.observation_id, "status": row.status, "namespace": row.namespace,
                "createdAt": row.created_at.isoformat(), "updatedAt": row.updated_at.isoformat()}

    def get(self, observation_id):
        with Session(self.engine) as db:
            row = db.get(FailureObservation, observation_id)
            return None if row is None else self._view(row)

    def active(self):
        with Session(self.engine) as db:
            row = db.scalar(select(FailureObservation).where(FailureObservation.active_slot == 1))
            return None if row is None else self._view(row)

    def save(self, observation_id, *, status, payload, ended):
        with Session(self.engine) as db:
            row = db.get(FailureObservation, observation_id)
            row.status, row.payload, row.updated_at = status, payload, now()
            if ended:
                row.active_slot = None
            db.commit()

    def history(self, *, limit=10):
        with Session(self.engine) as db:
            rows = db.scalars(select(FailureObservation).order_by(FailureObservation.created_at.desc(),
                                                                 FailureObservation.observation_id.desc()).limit(limit)).all()
            return [self._view(row) for row in rows]
