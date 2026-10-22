import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config

from app.main import app
from app.core.database import get_engine
from app.api.recommendations import get_current_evaluator, get_db, get_recommendation_provider
from app.engine.recommendation_evaluation_validation.approval import LIVE_EVIDENCE_SOURCE, LiveRecommendation
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

RANKED_SCORE = 0.8


def make_report(rec_id: str, feasible: bool = True) -> RecommendationReport:
    """One candidate. A feasible one is ranked first with a positive score, as the live pipeline would produce."""
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
                score=RANKED_SCORE if feasible else 0.0
            )
        ],
        selected_recommendation_id=rec_id
    )


LIVE_EVIDENCE = {
    "source": LIVE_EVIDENCE_SOURCE,
    "topologyCapturedAt": "2026-01-01T00:00:00Z",
    "connectionFingerprint": "test-connection",
    # What the server stores so it can re-evaluate the request when it is decided.
    "workflowId": "wf1",
    "namespace": "shop",
    "failedNode": "w1",
}


class LiveSource:
    """Stands in for the future live recommendation pipeline (test double behind a dependency)."""

    def __init__(self) -> None:
        self.report = make_report("default-rec")

    def set(self, rec_id: str, feasible: bool = True) -> None:
        self.report = make_report(rec_id, feasible)

    def __call__(self) -> LiveRecommendation:
        evidence = dict(LIVE_EVIDENCE)
        evidence["ranking"] = {"candidates": {
            c.id: {"id": c.id, "rank": 1 if c.feasible else None, "score": c.score if c.feasible else None,
                   "excluded": None if c.feasible else "INSUFFICIENT_EVIDENCE",
                   "components": {"blastBefore": 100.0, "blastAfter": 0.0}}
            for c in self.report.recommendations}}
        # The plan the evaluation proposes, which an approval is bound to.
        evidence["feasibility"] = {
            c.id: {"status": "FEASIBLE", "placement": {f"{c.id}-0": "n1", f"{c.id}-1": "n2"}, "podsToRecreate": [f"{c.id}-1"],
                   "constraintsDigest": "digest-a"}
            for c in self.report.recommendations if c.feasible}
        return LiveRecommendation(report=self.report, evidence=evidence)


@pytest.fixture
def mock_get_sample(override_get_db):
    """Kept under its old name for the tests below: a controllable *live* recommendation source."""
    source = LiveSource()
    app.dependency_overrides[get_recommendation_provider] = lambda: source
    app.dependency_overrides[get_current_evaluator] = lambda: (lambda approval: source())   # same evaluation
    source._get_mock = make_report
    yield source
    app.dependency_overrides.pop(get_recommendation_provider, None)
    app.dependency_overrides.pop(get_current_evaluator, None)

def test_request_approval_succeeds_and_get_returns_it(client: TestClient, mock_get_sample):
    recommendation_id = "rec-1"
    mock_get_sample.set(recommendation_id)
    
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
    mock_get_sample.set(recommendation_id)
    
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
    mock_get_sample.set(recommendation_id)
    
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
    mock_get_sample.set(recommendation_id)
    
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
    mock_get_sample.set("infeasible-rec", feasible=False)
    
    # 12. infeasible recommendation -> 422
    res = client.post("/api/recommendations/infeasible-rec/request-approval", json={"recommendationId": "infeasible-rec", "proposalVersion": "v1"})
    assert res.status_code == 422
