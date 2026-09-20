"""Minimal SQLAlchemy model proving the persistence infrastructure works end
to end (engine -> migrations -> a real table) on both SQLite and PostgreSQL.

This is intentionally a single, generic table, not a permanent design:
- It is NOT a substitute for future per-component tables (experiments,
  recommendations, validations) — those should be modeled explicitly once
  a component actually needs to persist something.
- No `app/engine/*` code imports this module. Research algorithms operate
  purely on the Pydantic schemas in `app/shared/schemas/` and stay
  database-independent.

Fields:
- id: surrogate primary key.
- artifact_type: free-text label for what was stored (e.g. "workflow_graph").
  Not yet constrained to an enum/FK since nothing writes here yet.
- payload: the artifact's JSON representation, stored via SQLAlchemy's
  dialect-agnostic `JSON` type (TEXT-backed on SQLite, native JSON on
  PostgreSQL) — no JSONB-specific features are used.
- created_at: UTC insertion timestamp, for basic ordering/inspection.

This is scaffolding, not a finished schema: fields are added when a real
migration needs them, not speculatively.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ResearchArtifact(Base):
    __tablename__ = "research_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
