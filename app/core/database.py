from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine, create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def is_sqlite_url(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def sqlite_file_path(database_url: str) -> Path:
    """Resolve the on-disk file path encoded in a sqlite:/// URL."""

    raw_path = database_url.split("///", 1)[1]
    path = Path(raw_path)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def ensure_sqlite_directory(database_url: str) -> Path:
    """Create the parent directory for a SQLite database file if it is missing.

    Returns the resolved database file path. The file itself is created
    later, as a side effect of the first connection (e.g. during migration).
    """

    db_path = sqlite_file_path(database_url)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def _enable_sqlite_wal(dbapi_connection, connection_record) -> None:
    # WAL lets concurrent readers proceed while a write is in progress,
    # which matters because FastAPI's threadpool can hit the same SQLite
    # file from more than one worker thread at once (the default rollback
    # journal would otherwise serialize/lock on every write).
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def get_engine(database_url: str) -> Engine:
    """Build a SQLAlchemy engine for either a sqlite:// or postgresql:// URL.

    Callers never need to branch on the database type: SQLite-specific setup
    (directory creation, WAL, thread-safety) is contained here.
    """

    if is_sqlite_url(database_url):
        ensure_sqlite_directory(database_url)
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _enable_sqlite_wal)
        return engine

    return create_engine(database_url)
