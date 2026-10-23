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
    # The rollout the change is waiting for (generation, updated/ready/available replicas per Deployment, as last
    # observed). Written and updated by observing the cluster; a patch being accepted is never recorded as a
    # completed rollout. NULL for a change recorded before rollout tracking existed.
    rollout: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    applied_by: Mapped[str] = mapped_column(String(100), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reset_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppliedChangeAnalysisRecord(Base):
    """The post-change failure-impact analysis of one applied change (Member 4), one row per direction.

    ``basis``, ``baseline`` and ``predicted`` are written when the change is applied (direction "apply") or reset
    (direction "reset") and never updated: the before-change blast radius and the approved proposal's prediction
    stay what they were when the person approved them. ``observed`` and ``comparison`` are written when the
    rollout of that direction has completed and Member 3's predictor has recalculated the impact from freshly
    observed topology. This is a model recalculation over live placement, NOT an experimentally observed failure
    rate; ``label`` says so on the row itself.
    """

    __tablename__ = "applied_change_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    application_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    recommendation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)          # "apply" | "reset"
    status: Mapped[str] = mapped_column(String(20), nullable=False)             # PENDING | COMPLETED | FAILED | UNAVAILABLE

    basis: Mapped[dict] = mapped_column(JSON, nullable=False)                   # workflow, namespace, failed node, provenance
    baseline: Mapped[dict] = mapped_column(JSON, nullable=False)                # pre-change impact, immutable
    predicted: Mapped[dict | None] = mapped_column(JSON, nullable=True)         # the approved proposal's what-if result
    observed: Mapped[dict | None] = mapped_column(JSON, nullable=True)          # recalculated from post-rollout topology
    comparison: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    label: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ValidationLoopRunRecord(Base):
    """One fault-injection validation run (Member 4): the approval it ran under, its plan, its progress and its outcome.

    ``approval`` is the run grant (plan digest, cluster UID, workers, candidates, the largest number of node failures,
    time of approval); it carries no person's name. ``kills_used`` is the durable counter every node failure is charged
    to before anything is armed, so a restart cannot reset the budget. ``restore_points`` holds the undo information of
    the Deployments a candidate wrote, stored BEFORE the first write so an interrupted run can be put back. ``current``
    is what the run is doing right now (candidate, worker, repeat, stage), for the page. ``recovery`` records the
    outcome of recovering an interrupted run. Written by the run's own worker and by recovery, never by a page request.
    """

    __tablename__ = "validation_loop_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(30), nullable=False)       # DISPOSABLE | DOCKER_DESKTOP
    target_name: Mapped[str] = mapped_column(String(100), nullable=False)
    cluster_uid: Mapped[str] = mapped_column(String(100), nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(100), nullable=False)
    approval: Mapped[dict] = mapped_column(JSON, nullable=False)
    plan: Mapped[dict] = mapped_column(JSON, nullable=False)
    kills_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    abort_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    restore_points: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recovery: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    events: Mapped[list] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ValidationLoopExperimentRecord(Base):
    """One planned node-failure experiment of a run (candidate x worker x repeat, or a baseline), with its outcome.

    Rows are written PENDING when the run is planned, so a skipped experiment is a row with status SKIPPED and a reason,
    never an absent one. ``experiment_id`` links to the ``validation_runs`` record when a full record was written.
    """

    __tablename__ = "validation_loop_experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)              # baseline | candidate
    candidate_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    worker: Mapped[str] = mapped_column(String(100), nullable=False)
    repeat: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    experiment_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
