import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from unittest.mock import patch

from app.main import app
from app.core.database import get_engine
from app.api.recommendations import get_db
from sqlalchemy.orm import Session
from app.shared.schemas.recommendation import RecommendationReport, RecommendationCandidate
from datetime import datetime, timezone

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
def override_get_db(sqlite_url: str):
    engine = get_engine(sqlite_url)
    def _override():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_db] = _override
    yield
    app.dependency_overrides.pop(get_db, None)

@pytest.fixture
def client(override_get_db) -> TestClient:
    return TestClient(app)

@pytest.fixture
def mock_get_sample():
    with patch("app.api.recommendations.get_sample_recommendation") as mock:
        def _get_mock(rec_id: str, feasible: bool = True):
            return RecommendationReport(
                workflow_id="wf1",
                recommendations=[
                    RecommendationCandidate(
                        id=rec_id,
                        type="TOPOLOGY_SPREAD",
                        service="test",
                        feasible=feasible,
                        predicted_blast_radius=0.5,
                        risk_reduction=0.0,
                        resource_cost="high",
                        confidence=0.0,
                        score=0.0
                    )
                ],
                selected_recommendation_id=rec_id
            )
        mock.side_effect = lambda: _get_mock("default-rec")
        mock._get_mock = _get_mock
        yield mock

def test_request_approval_succeeds_and_get_returns_it(client: TestClient, mock_get_sample):
    recommendation_id = "rec-1"
    mock_get_sample.side_effect = lambda: mock_get_sample._get_mock(recommendation_id)
    
    # 1. request approval succeeds
    req_payload = {"recommendationId": recommendation_id, "proposalVersion": "v1", "yamlSnapshot": "apiVersion: v1"}
    response = client.post(f"/api/recommendations/{recommendation_id}/request-approval", json=req_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["recommendationId"] == recommendation_id
    assert data["status"] == "PENDING_APPROVAL"
    assert "id" in data
    
    # 2. GET returns persisted PENDING_APPROVAL record
    get_res = client.get(f"/api/recommendations/{recommendation_id}/approval")
    assert get_res.status_code == 200
    assert get_res.json()["status"] == "PENDING_APPROVAL"

def test_approve_succeeds_after_request(client: TestClient, mock_get_sample):
    recommendation_id = "rec-approve-1"
    mock_get_sample.side_effect = lambda: mock_get_sample._get_mock(recommendation_id)
    
    # request
    client.post(f"/api/recommendations/{recommendation_id}/request-approval", json={"recommendationId": recommendation_id, "proposalVersion": "v1"})
    
    # 3. approve succeeds
    approve_payload = {"expectedProposalVersion": "v1", "note": "LGTM"}
    approve_res = client.post(f"/api/recommendations/{recommendation_id}/approve", json=approve_payload)
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "APPROVED"
    assert approve_res.json()["decisionNote"] == "LGTM"
    
    # 4. GET returns APPROVED after commit
    get_res = client.get(f"/api/recommendations/{recommendation_id}/approval")
    assert get_res.status_code == 200
    assert get_res.json()["status"] == "APPROVED"
    assert get_res.json()["decisionNote"] == "LGTM"

def test_reject_succeeds_after_request(client: TestClient, mock_get_sample):
    recommendation_id = "rec-reject-1"
    mock_get_sample.side_effect = lambda: mock_get_sample._get_mock(recommendation_id)
    
    # request
    client.post(f"/api/recommendations/{recommendation_id}/request-approval", json={"recommendationId": recommendation_id, "proposalVersion": "v1"})
    
    # 5. reject succeeds on a fresh pending proposal
    reject_payload = {"expectedProposalVersion": "v1", "note": "Needs work"}
    reject_res = client.post(f"/api/recommendations/{recommendation_id}/reject", json=reject_payload)
    assert reject_res.status_code == 200
    assert reject_res.json()["status"] == "REJECTED"
    
    # 6. GET returns REJECTED after commit
    get_res = client.get(f"/api/recommendations/{recommendation_id}/approval")
    assert get_res.status_code == 200
    assert get_res.json()["status"] == "REJECTED"

def test_failures(client: TestClient, mock_get_sample):
    recommendation_id = "rec-fail-1"
    mock_get_sample.side_effect = lambda: mock_get_sample._get_mock(recommendation_id)
    
    # 7. unknown approval -> 404
    res = client.get("/api/recommendations/unknown-id/approval")
    assert res.status_code == 404
    
    # request approval
    client.post(f"/api/recommendations/{recommendation_id}/request-approval", json={"recommendationId": recommendation_id, "proposalVersion": "v1"})
    
    # 8. duplicate request -> 409
    res = client.post(f"/api/recommendations/{recommendation_id}/request-approval", json={"recommendationId": recommendation_id, "proposalVersion": "v2"})
    assert res.status_code == 409
    
    # 11. proposal-version mismatch -> 409
    res = client.post(f"/api/recommendations/{recommendation_id}/approve", json={"expectedProposalVersion": "v2", "note": ""})
    assert res.status_code == 409
    
    # approve it
    client.post(f"/api/recommendations/{recommendation_id}/approve", json={"expectedProposalVersion": "v1", "note": ""})
    
    # 9. approve already-approved proposal -> 409
    res = client.post(f"/api/recommendations/{recommendation_id}/approve", json={"expectedProposalVersion": "v1", "note": ""})
    assert res.status_code == 409
    
    # 10. reject already-approved proposal -> 409 (Wait, reject already rejected? Actually rejecting an approved is also 409)
    res = client.post(f"/api/recommendations/{recommendation_id}/reject", json={"expectedProposalVersion": "v1", "note": ""})
    assert res.status_code == 409
    
    # 13. malformed request body -> 422
    res = client.post(f"/api/recommendations/{recommendation_id}/request-approval", json={"invalid": "payload"})
    assert res.status_code == 422

def test_infeasible_recommendation_422(mock_get_sample, client: TestClient):
    mock_get_sample.side_effect = lambda: mock_get_sample._get_mock("infeasible-rec", feasible=False)
    
    # 12. infeasible recommendation -> 422
    res = client.post("/api/recommendations/infeasible-rec/request-approval", json={"recommendationId": "infeasible-rec", "proposalVersion": "v1"})
    assert res.status_code == 422
