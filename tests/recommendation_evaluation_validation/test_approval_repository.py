import pytest
from datetime import datetime, timezone
from pathlib import Path
from alembic import command
from alembic.config import Config

from app.core.database import get_engine
from sqlalchemy.orm import Session
from app.engine.recommendation_evaluation_validation.approval.repository import ApprovalRepository
from app.engine.recommendation_evaluation_validation.approval.models import ApprovalModel
from app.shared.schemas.approval import ApprovalStatus
from app.shared.persistence.models import RecommendationApprovalRecord

PROJECT_ROOT = Path(__file__).resolve().parents[2]

def _alembic_config() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg

@pytest.fixture
def sqlite_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(_alembic_config(), "head")
    return database_url

@pytest.fixture
def db_session(sqlite_url: str):
    engine = get_engine(sqlite_url)
    with Session(engine) as session:
        yield session

def test_repository_creates_and_retrieves_approval(db_session: Session):
    repository = ApprovalRepository(db_session)
    
    recommendation_snapshot = {
        "service": "paymentservice",
        "recommendation_type": "TOPOLOGY_SPREAD",
        "reason": "Replica concentration",
        "feasible": True,
    }
    evaluation_snapshot = {
        "concentration_before": 0.67,
        "concentration_after": 0.33,
    }

    requested_at = datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    model = ApprovalModel(
        id=0, # Ignored on create
        recommendation_id="rec-123",
        proposal_version="v1",
        status=ApprovalStatus.PENDING_APPROVAL,
        requested_at=requested_at,
        requested_by="user1",
        recommendation_snapshot=recommendation_snapshot,
        yaml_snapshot="apiVersion: apps/v1",
        evaluation_snapshot=evaluation_snapshot,
    )
    
    saved = repository.save(model)
    assert saved.id is not None
    assert saved.id > 0
    
    db_session.commit()
    
    # Use a new session to ensure it's loaded from DB
    new_session = Session(db_session.get_bind())
    new_repository = ApprovalRepository(new_session)
    
    loaded = new_repository.get_by_id(saved.id)
    assert loaded is not None
    assert loaded.recommendation_id == "rec-123"
    assert loaded.proposal_version == "v1"
    assert loaded.status == ApprovalStatus.PENDING_APPROVAL
    assert loaded.requested_at == requested_at
    assert loaded.requested_by == "user1"
    
    assert loaded.recommendation_snapshot == recommendation_snapshot
    assert loaded.yaml_snapshot == "apiVersion: apps/v1"
    assert loaded.evaluation_snapshot == evaluation_snapshot
    
    new_session.close()

def test_repository_updates_status(db_session: Session):
    repository = ApprovalRepository(db_session)
    requested_at = datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc)
    model = ApprovalModel(
        id=0,
        recommendation_id="rec-456",
        proposal_version="v1",
        status=ApprovalStatus.PENDING_APPROVAL,
        requested_at=requested_at,
        requested_by="user1",
        recommendation_snapshot={"key": "val"},
        evaluation_snapshot=None,
    )
    saved = repository.save(model)
    db_session.commit()
    
    saved.status = ApprovalStatus.APPROVED
    saved.decided_at = datetime(2023, 1, 2, 12, 0, tzinfo=timezone.utc)
    saved.decided_by = "admin"
    saved.decision_note = "Looks good"
    
    updated = repository.update(saved)
    db_session.commit()
    
    new_session = Session(db_session.get_bind())
    new_repository = ApprovalRepository(new_session)
    
    loaded = new_repository.get_by_id(updated.id)
    assert loaded is not None
    assert loaded.status == ApprovalStatus.APPROVED
    assert loaded.decided_at == saved.decided_at
    assert loaded.decided_by == "admin"
    assert loaded.decision_note == "Looks good"
    new_session.close()

def test_immutable_snapshot_behavior(db_session: Session):
    repository = ApprovalRepository(db_session)
    
    # Original dict reference
    original_recommendation_snapshot = {
        "service": "paymentservice",
        "feasible": True,
    }
    
    requested_at = datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc)
    model = ApprovalModel(
        id=0,
        recommendation_id="rec-immutable",
        proposal_version="v1",
        status=ApprovalStatus.PENDING_APPROVAL,
        requested_at=requested_at,
        requested_by="user1",
        recommendation_snapshot=original_recommendation_snapshot,
    )
    saved = repository.save(model)
    db_session.commit()
    
    # Modify original object
    original_recommendation_snapshot["feasible"] = False
    original_recommendation_snapshot["injected_field"] = "hacked"
    
    # Reload from fresh session
    new_session = Session(db_session.get_bind())
    new_repository = ApprovalRepository(new_session)
    
    loaded = new_repository.get_by_id(saved.id)
    assert loaded is not None
    # Stored snapshot MUST still contain original values, not modified
    assert loaded.recommendation_snapshot["feasible"] is True
    assert "injected_field" not in loaded.recommendation_snapshot
    new_session.close()
