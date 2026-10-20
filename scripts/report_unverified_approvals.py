"""Read-only audit of stored approval records.

    DATABASE_URL=sqlite:////data/resilience.db python scripts/report_unverified_approvals.py

Prints one JSON object that classifies every row of ``recommendation_approvals`` as ``verified`` (it
carries the server-written live-evidence marker) or ``unverified`` (anything else, including records
whose ids merely look real). It only runs SELECT statements; SQLite databases are opened in read-only
mode, so a missing file is an error and is never created. Nothing is deleted or changed.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, inspect  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.engine.recommendation_evaluation_validation.approval import audit_approvals  # noqa: E402

SQLITE_PREFIX = "sqlite:///"


def read_only_engine(database_url: str):
    if not database_url.startswith(SQLITE_PREFIX):
        return create_engine(database_url)
    path = Path(database_url.removeprefix(SQLITE_PREFIX))
    if not path.is_file():
        raise SystemExit(f"Database file not found: {path}")
    return create_engine(f"{SQLITE_PREFIX}file:{path.resolve()}?mode=ro&uri=true")


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Set DATABASE_URL to the database to audit.", file=sys.stderr)
        return 2
    engine = read_only_engine(database_url)
    if "recommendation_approvals" not in inspect(engine).get_table_names():
        rows = []
    else:
        with Session(engine) as session:
            rows = audit_approvals(session)
    verified = sum(1 for row in rows if row["provenance"] == "verified")
    print(json.dumps({
        "total": len(rows),
        "verified": verified,
        "unverified": len(rows) - verified,
        "records": rows,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
