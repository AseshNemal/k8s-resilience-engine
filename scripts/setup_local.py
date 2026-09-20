"""Bring the local database up to date: `python scripts/setup_local.py`.

Reads DATABASE_URL (from .env or the environment, defaulting to a local
SQLite file), creates the data/ directory for SQLite if needed, and runs
Alembic migrations up to head. Safe to run repeatedly: it only applies
pending migrations and is a no-op once already at head.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import ensure_sqlite_directory, get_engine, is_sqlite_url  # noqa: E402


def _alembic_config() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


def _current_revision(database_url: str) -> str | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def main() -> None:
    database_url = get_settings().database_url
    backend = "SQLite" if is_sqlite_url(database_url) else "PostgreSQL"

    print(f"Backend: {backend}")
    print(f"DATABASE_URL: {database_url}")

    if is_sqlite_url(database_url):
        db_path = ensure_sqlite_directory(database_url)
        status = "found existing database file" if db_path.exists() else "no database file yet, will be created"
        print(f"SQLite file: {db_path} ({status})")
    else:
        print("Connecting to external PostgreSQL server")

    before_revision = _current_revision(database_url)
    print(f"Current migration revision: {before_revision or '(none, fresh database)'}")

    command.upgrade(_alembic_config(), "head")

    after_revision = _current_revision(database_url)
    print(f"Migration status: now at revision {after_revision}")
    print("Local database setup complete.")


if __name__ == "__main__":
    main()
