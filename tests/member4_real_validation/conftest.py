from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.core.database import get_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'stack.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(config, "head")
    with Session(get_engine(url)) as db:
        yield db
