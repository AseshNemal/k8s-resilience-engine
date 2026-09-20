"""Optional PostgreSQL driver check.

Skipped entirely unless the optional PostgreSQL driver is installed
(pip install -r requirements-postgres.txt). This never needs a live
PostgreSQL server: constructing a SQLAlchemy Engine only resolves the
dialect and imports the DBAPI driver, it does not open a connection.

The always-run PostgreSQL coverage (dialect/URL resolution and get_engine's
branching) lives in tests/test_database.py and does not depend on this
driver being present.
"""

import pytest

pytest.importorskip("psycopg2")

from app.core.database import get_engine  # noqa: E402


def test_get_engine_builds_a_real_postgresql_engine_without_a_live_server() -> None:
    engine = get_engine("postgresql://user:pass@localhost:5432/resilience_engine")

    assert engine.dialect.name == "postgresql"
    assert engine.url.get_backend_name() == "postgresql"
