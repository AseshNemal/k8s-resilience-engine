from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.database import ensure_sqlite_directory, get_engine, is_sqlite_url

POSTGRESQL_URL = "postgresql://user:pass@localhost:5432/resilience_engine"


def test_sqlite_engine_creation_connects_and_executes(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'engine_test.db'}"

    engine = get_engine(database_url)

    assert engine.dialect.name == "sqlite"
    with engine.connect() as connection:
        assert connection.execute(text("select 1")).scalar() == 1


def test_postgresql_url_resolves_to_the_postgresql_backend() -> None:
    # make_url() only parses the URL string; it never imports a DBAPI
    # driver, so this proves dialect selection with no PostgreSQL driver
    # installed and no live server needed.
    assert not is_sqlite_url(POSTGRESQL_URL)
    assert make_url(POSTGRESQL_URL).get_backend_name() == "postgresql"


def test_get_engine_routes_postgresql_urls_to_plain_create_engine() -> None:
    """get_engine() must apply no SQLite-specific setup for a non-SQLite URL.

    Verified at the create_engine() call boundary (mocked) so this test
    never needs the optional PostgreSQL driver (psycopg2-binary) installed.
    A real-driver check lives in test_database_postgresql_optional.py.
    """

    sentinel = object()
    with patch("app.core.database.create_engine", return_value=sentinel) as mock_create_engine:
        engine = get_engine(POSTGRESQL_URL)

    mock_create_engine.assert_called_once_with(POSTGRESQL_URL)
    assert engine is sentinel


def test_sqlite_directory_is_created_when_missing(tmp_path: Path) -> None:
    target_dir = tmp_path / "nested" / "data"
    database_url = f"sqlite:///{target_dir / 'resilience.db'}"
    assert not target_dir.exists()

    resolved_path = ensure_sqlite_directory(database_url)

    assert target_dir.exists()
    assert resolved_path == target_dir / "resilience.db"


def test_get_engine_creates_sqlite_directory_as_a_side_effect(tmp_path: Path) -> None:
    target_dir = tmp_path / "auto-created"
    database_url = f"sqlite:///{target_dir / 'resilience.db'}"

    get_engine(database_url)

    assert target_dir.exists()
