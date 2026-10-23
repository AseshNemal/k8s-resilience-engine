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

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String
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


class RecommendationApprovalRecord(Base):
    __tablename__ = "recommendation_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    proposal_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)

    recommendation_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    yaml_snapshot: Mapped[str | None] = mapped_column(String, nullable=True)
    evaluation_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False)

    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String, nullable=True)


class ValidationRunRecord(Base):
    """One validation experiment (Member 4). The prediction and the observation live in separate columns on purpose.

    ``predicted``, ``proposal``, ``recommendation`` and ``before_evidence`` are written once, when the experiment is
    planned, and never updated. ``observed``, ``comparison``, ``prediction_error``, ``failure`` and the verdict are
    written once, when it ends. ``evidence_class`` says whether the observation came from a real cluster
    (REAL_CLUSTER) or from a test double (TEST_DOUBLE); only the former can ever be shown as an experimental result.
    ``validated`` and ``prediction_error`` stay NULL for any run that produced no measurement.
    """

    __tablename__ = "validation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    recommendation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workflow_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    validated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_measurement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_class: Mapped[str] = mapped_column(String(30), nullable=False)

    cluster: Mapped[dict] = mapped_column(JSON, nullable=False)
    workflow: Mapped[dict] = mapped_column(JSON, nullable=False)
    before_evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    recommendation: Mapped[dict] = mapped_column(JSON, nullable=False)
    proposal: Mapped[dict] = mapped_column(JSON, nullable=False)

    predicted: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    comparison: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prediction_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    events: Mapped[list] = mapped_column(JSON, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppliedChangeRecord(Base):
    """One approved change applied to the connected cluster, with the state it replaced so it can be reset (Member 4).

    ``before`` is written BEFORE the first write to the cluster (status APPLYING), so a crash half-way still leaves
    everything needed to put it back. ``status`` moves APPLYING -> APPLIED -> RESET; FAILED means the apply did not
    complete and everything it touched was put back; RESET_FAILED means a restore did not complete and can be retried.
    """

    __tablename__ = "applied_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    recommendation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    approval_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    yaml_applied: Mapped[str] = mapped_column(String, nullable=False)
    yaml_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[list] = mapped_column(JSON, nullable=False)
    cluster: Mapped[dict] = mapped_column(JSON, nullable=False)
    events: Mapped[list] = mapped_column(JSON, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    applied_by: Mapped[str] = mapped_column(String(100), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reset_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
