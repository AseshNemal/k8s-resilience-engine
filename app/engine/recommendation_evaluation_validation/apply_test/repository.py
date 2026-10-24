from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence.models import Base


def now():
    return datetime.now(timezone.utc)


class ApplyTestRun(Base):
    __tablename__ = "apply_test_runs"
    run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(80), unique=True)
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RunRepository:
    """Each operation owns its session; a worker never reuses a request's session."""
    def __init__(self, engine):
        self.engine = engine

    def create(self, run_id, request_key, payload):
        with Session(self.engine) as db:
            row = ApplyTestRun(run_id=run_id, request_key=request_key, active_slot=1, status="QUEUED", payload=payload)
            db.add(row)
            db.commit()

    def get(self, run_id=None, *, request_key=None):
        with Session(self.engine) as db:
            query = select(ApplyTestRun)
            if run_id:
                query = query.where(ApplyTestRun.run_id == run_id)
            elif request_key:
                query = query.where(ApplyTestRun.request_key == request_key)
            else:
                query = query.order_by(ApplyTestRun.created_at.desc())
            row = db.scalar(query)
            return None if row is None else {**row.payload, "runId": row.run_id, "requestKey": row.request_key, "status": row.status,
                "createdAt": row.created_at.isoformat(), "updatedAt": row.updated_at.isoformat()}

    def update(self, run_id, **values):
        with Session(self.engine) as db:
            row = db.get(ApplyTestRun, run_id)
            payload = dict(row.payload)
            if "event" in values:
                event = {"at": now().isoformat(), **values.pop("event")}
                payload["events"] = [*payload.get("events", []), event]
                payload["current"] = event
            if "status" in values:
                row.status = values.pop("status")
                if row.status not in ("QUEUED", "RUNNING"):
                    row.active_slot = None
            row.payload = {**payload, **values}
            row.updated_at = now()
            db.commit()

    def active(self):
        with Session(self.engine) as db:
            return db.scalar(select(ApplyTestRun.run_id).where(ApplyTestRun.active_slot == 1))

    def history(self, *, limit=20, offset=0):
        with Session(self.engine) as db:
            rows = db.scalars(select(ApplyTestRun).order_by(ApplyTestRun.created_at.desc(), ApplyTestRun.run_id.desc())
                              .offset(offset).limit(limit + 1)).all()
            return ([{**row.payload, "runId": row.run_id, "status": row.status,
                      "createdAt": row.created_at.isoformat()} for row in rows[:limit]], len(rows) > limit)
