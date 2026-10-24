from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from app.core.database import get_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEAD_REVISION = "b71d8ea210c4"


def _alembic_config() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


def _current_revision(database_url: str) -> str | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


@pytest.fixture
def sqlite_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A fresh, isolated SQLite URL with DATABASE_URL pointed at it.

    alembic/env.py reads DATABASE_URL via app.core.config.get_settings(), so
    tests control which database a migration run targets by setting this
    environment variable rather than by touching the project's real data/.
    """

    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    return database_url


def test_fresh_database_has_no_revision_before_migrating(sqlite_url: str) -> None:
    assert _current_revision(sqlite_url) is None


def test_first_setup_migrates_fresh_database_to_head(sqlite_url: str) -> None:
    command.upgrade(_alembic_config(), "head")

    assert _current_revision(sqlite_url) == HEAD_REVISION

    engine = get_engine(sqlite_url)
    assert inspect(engine).has_table("research_artifacts")
    columns = {col["name"] for col in inspect(engine).get_columns("research_artifacts")}
    assert columns == {"id", "artifact_type", "payload", "created_at"}
    
    assert inspect(engine).has_table("recommendation_approvals")
    columns = {col["name"] for col in inspect(engine).get_columns("recommendation_approvals")}
    assert columns == {"id", "recommendation_id", "proposal_version", "status", "recommendation_snapshot", "yaml_snapshot", "evaluation_snapshot", "requested_at", "requested_by", "decided_at", "decided_by", "decision_note"}

    assert inspect(engine).has_table("validation_runs")
    assert inspect(engine).has_table("apply_test_runs")

    # Member 4: rollout tracking on an applied change, and the post-change analyses (additive, nullable where they extend a table).
    assert "rollout" in {col["name"] for col in inspect(engine).get_columns("applied_changes")}
    analyses = {col["name"] for col in inspect(engine).get_columns("applied_change_analyses")}
    assert {"analysis_id", "application_id", "direction", "status", "basis", "baseline", "predicted", "observed", "comparison",
            "error", "label", "created_at", "completed_at"} <= analyses


def test_repeated_setup_when_already_at_head_is_a_noop(sqlite_url: str) -> None:
    command.upgrade(_alembic_config(), "head")
    first_revision = _current_revision(sqlite_url)

    # Running upgrade("head") again when already there must succeed cleanly.
    command.upgrade(_alembic_config(), "head")
    second_revision = _current_revision(sqlite_url)

    assert first_revision == second_revision == HEAD_REVISION


# Pending-migration / data-preservation behavior (upgrading a populated
# database across a real schema change) will be tested when the first
# genuine schema-changing migration lands, using real data, not a
# migration added solely to exercise the test.
