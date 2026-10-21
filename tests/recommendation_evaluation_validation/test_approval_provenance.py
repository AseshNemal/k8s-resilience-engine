"""Approvals must be bound to live recommendation evidence; legacy records stay visible but unverified."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.approval import (
    ApprovalModel,
    ApprovalRepository,
    audit_approvals,
)
from app.main import app
from app.shared.schemas.approval import ApprovalStatus
from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    mock_get_sample,
    override_get_db,
    sqlite_url,
)


def legacy_record(session: Session, recommendation_id: str, *, status=ApprovalStatus.PENDING_APPROVAL):
    """A row written before provenance existed: it has no provenance marker."""
    return ApprovalRepository(session).save(ApprovalModel(
        id=0, recommendation_id=recommendation_id, proposal_version="v1", status=status,
        requested_at=datetime.now(timezone.utc), requested_by="developer",
        recommendation_snapshot={"id": recommendation_id, "type": "TOPOLOGY_SPREAD"},
        evaluation_snapshot={"score": 0.9},
    ))


@pytest.fixture
def session(sqlite_url):
    engine = get_engine(sqlite_url)
    with Session(engine) as db:
        yield db


def test_without_a_live_recommendation_source_nothing_can_be_requested(client: TestClient, session):
    # No dependency override: this is the production wiring until a live pipeline exists.
    response = client.post("/api/recommendations/rec-001/request-approval",
                           json={"recommendationId": "rec-001", "proposalVersion": "v1"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RECOMMENDATION_EVIDENCE_UNAVAILABLE"
    assert ApprovalRepository(session).get_by_recommendation_id("rec-001") is None


def test_the_sample_recommendation_is_never_a_substitute(client: TestClient):
    response = client.post("/api/recommendations/rec-001/request-approval",
                           json={"recommendationId": "rec-001", "proposalVersion": "v1"})

    assert response.status_code == 409
    assert "snapshot" not in response.text.lower()


def test_provenance_comes_from_the_server_and_is_stored_with_the_approval(client: TestClient, mock_get_sample):
    mock_get_sample.set("rec-live")
    client.post("/api/recommendations/rec-live/request-approval", json={
        "recommendationId": "rec-live", "proposalVersion": "v1",
        "provenance": {"source": "client-says-so"},  # must be ignored
    })

    data = client.get("/api/recommendations/rec-live/approval")

    provenance = data.json()["evaluationSnapshot"]["provenance"]
    assert provenance["source"] == "live-discovery"
    assert provenance["connectionFingerprint"] == "test-connection"
    assert data.headers["X-Evidence-Provenance"] == "verified"


def test_legacy_pending_record_is_visible_but_labelled_unverified(client: TestClient, session):
    legacy_record(session, "rec-old")

    response = client.get("/api/recommendations/rec-old/approval")

    assert response.status_code == 200
    assert response.headers["X-Evidence-Provenance"] == "unverified"


def test_legacy_record_cannot_be_approved(client: TestClient, session):
    legacy_record(session, "rec-old")

    response = client.post("/api/recommendations/rec-old/approve",
                           json={"expectedProposalVersion": "v1", "note": "go"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "APPROVAL_PROVENANCE_UNVERIFIED"
    still = client.get("/api/recommendations/rec-old/approval").json()
    assert still["status"] == "PENDING_APPROVAL"


def test_legacy_record_can_still_be_rejected_because_rejecting_applies_nothing(client: TestClient, session):
    legacy_record(session, "rec-old")

    response = client.post("/api/recommendations/rec-old/reject",
                           json={"expectedProposalVersion": "v1", "note": "not trusted"})

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"


def test_records_are_never_deleted_by_these_paths(client: TestClient, session):
    legacy_record(session, "rec-old")
    client.post("/api/recommendations/rec-old/approve", json={"expectedProposalVersion": "v1", "note": ""})

    assert ApprovalRepository(session).get_by_recommendation_id("rec-old") is not None


# ---- read-only audit ------------------------------------------------------------------------------------

def test_audit_classifies_every_record_without_assuming_unknown_ids_are_live(client: TestClient, session, mock_get_sample):
    legacy_record(session, "rec-001")          # the known fixture id
    legacy_record(session, "rec-looks-real")   # different id, still no provenance
    mock_get_sample.set("rec-live")
    client.post("/api/recommendations/rec-live/request-approval",
                json={"recommendationId": "rec-live", "proposalVersion": "v1"})

    rows = {r["recommendationId"]: r for r in audit_approvals(session)}

    assert rows["rec-001"]["provenance"] == "unverified"
    assert rows["rec-001"]["knownFixtureId"] is True
    assert rows["rec-looks-real"]["provenance"] == "unverified"
    assert rows["rec-looks-real"]["knownFixtureId"] is False
    assert rows["rec-live"]["provenance"] == "verified"


def test_audit_is_read_only(session):
    legacy_record(session, "rec-old")
    before = ApprovalRepository(session).get_by_recommendation_id("rec-old")

    audit_approvals(session)

    after = ApprovalRepository(session).get_by_recommendation_id("rec-old")
    assert (after.status, after.evaluation_snapshot, after.decided_at) == (
        before.status, before.evaluation_snapshot, before.decided_at)


# ---- the page reads the same provider --------------------------------------------------------------------

def test_live_recommendation_endpoint_reports_missing_evidence_explicitly(client: TestClient):
    response = client.get("/api/recommendations/live")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RECOMMENDATION_EVIDENCE_UNAVAILABLE"


def test_live_recommendation_endpoint_serves_only_what_the_provider_supplies(client: TestClient, mock_get_sample):
    mock_get_sample.set("rec-live")

    response = client.get("/api/recommendations/live")

    assert response.status_code == 200
    body = response.json()
    assert body["selectedRecommendationId"] == "rec-live"
    assert body["recommendations"][0]["id"] == "rec-live"
    assert response.headers["X-Evidence-Source"] == "live-discovery"
    assert response.headers["X-Topology-Captured-At"] == "2026-01-01T00:00:00Z"
