"""The audit CLI reports approval provenance and never writes to the database."""

import hashlib
import os
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.approval import ApprovalModel, ApprovalRepository
from app.shared.schemas.approval import ApprovalStatus
from tests.recommendation_evaluation_validation.test_approval_api import sqlite_url  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]


def os_essentials() -> dict:
    """Variables a Windows process needs even when the test builds its environment by hand: without SYSTEMROOT
    the network stack cannot start, so importing asyncio fails with WinError 10106."""
    return {k: os.environ[k] for k in ("SYSTEMROOT", "SystemDrive") if k in os.environ}


def run_report(database_url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "report_unverified_approvals.py"), *args],
        capture_output=True, text=True, cwd=ROOT, env={"DATABASE_URL": database_url, "PATH": "/usr/bin:/bin", **os_essentials()},
    )


def seed(url: str) -> Path:
    with Session(get_engine(url)) as db:
        ApprovalRepository(db).save(ApprovalModel(
            id=0, recommendation_id="rec-001", proposal_version="v1", status=ApprovalStatus.PENDING_APPROVAL,
            requested_at=datetime.now(timezone.utc), requested_by="developer",
            recommendation_snapshot={"id": "rec-001"}, evaluation_snapshot={"score": 1},
        ))
    return Path(url.removeprefix("sqlite:///"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reports_each_record_with_its_provenance_and_changes_nothing(sqlite_url):
    database = seed(sqlite_url)
    before = digest(database)

    result = run_report(sqlite_url)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["total"] == 1
    assert report["unverified"] == 1
    assert report["records"][0]["recommendationId"] == "rec-001"
    assert report["records"][0]["provenance"] == "unverified"
    assert report["records"][0]["knownFixtureId"] is True
    assert digest(database) == before


def test_an_empty_database_is_reported_as_empty_not_as_an_error(sqlite_url):
    result = run_report(sqlite_url)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"total": 0, "verified": 0, "unverified": 0, "records": []}


def test_a_missing_database_is_an_error_and_is_not_created(tmp_path):
    missing = tmp_path / "nope.db"

    result = run_report(f"sqlite:///{missing}")

    assert result.returncode != 0
    assert not missing.exists()
