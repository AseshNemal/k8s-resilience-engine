import pytest
from fastapi.testclient import TestClient
from tests.recommendation_evaluation_validation.test_approval_api import override_get_db, sqlite_url, mock_get_sample, client

def test_client_cannot_override_canonical_facts(client: TestClient, mock_get_sample):
    """
    Test Client Integrity Boundary:
    Verify that client-submitted requests cannot override canonical research recommendation facts.
    """
    recommendation_id = "rec-integrity-1"
    
    # Configure the mock to return specific canonical facts
    mock_get_sample.set(recommendation_id, feasible=True)
    
    # 1. Client tries to inject fake values in the request (e.g. via extra fields or yamlSnapshot)
    req_payload = {
        "recommendationId": recommendation_id,
        "proposalVersion": "v1",
        "yamlSnapshot": "apiVersion: v1\nkind: Fake\nscore: 999.0", # Trying to override score or something
        "fakeField": "should-be-ignored"
    }
    
    response = client.post(f"/api/recommendations/{recommendation_id}/request-approval", json=req_payload)
    assert response.status_code == 200
    
    # 2. Fetch the created record
    get_res = client.get(f"/api/recommendations/{recommendation_id}/approval")
    assert get_res.status_code == 200
    data = get_res.json()
    
    # 3. Assert the canonical facts are still what the backend provided
    # The recommendation_snapshot should contain the canonical data from the live recommendation source
    snapshot = data["recommendationSnapshot"]
    
    # In mock_get_sample, the ranked candidate's score is 0.8 and blast radius is 0.5 (a zero score would not
    # be approvable at all any more)
    assert snapshot["score"] == pytest.approx(0.8)
    assert snapshot["predicted_blast_radius"] == 0.5
    assert "fakeField" not in snapshot
    
    # The client's yamlSnapshot is saved, but it doesn't pollute the recommendationSnapshot
    assert data["yamlSnapshot"] == req_payload["yamlSnapshot"]
