"""Fixtures shared by the validation-loop tests: a migrated SQLite database and a repository over it."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.validation_loop.repository import LoopRepository

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'loop.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")
    engine = get_engine(url)
    yield LoopRepository(lambda: Session(engine), clock=lambda: NOW)


