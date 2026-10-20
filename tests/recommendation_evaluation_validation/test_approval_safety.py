import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.api.recommendations import get_db
from app.main import app
from tests.recommendation_evaluation_validation.test_approval_api import override_get_db, sqlite_url, mock_get_sample, client

def test_approval_never_auto_applies(client: TestClient, mock_get_sample):
    """
    Explicit No-Auto-Apply Safety Test.
    Proves that the approval path has no dependencies on Kubernetes application code/logic
    and does not attempt to execute Kubernetes changes.
    """
    recommendation_id = "rec-safety-1"
    mock_get_sample.set(recommendation_id)
    
    # 1. Request approval
    client.post(f"/api/recommendations/{recommendation_id}/request-approval", json={"recommendationId": recommendation_id, "proposalVersion": "v1"})
    
    # 2. Patch subprocess and the Kubernetes REST client to ensure they are NOT called
    with patch("subprocess.run") as mock_subprocess_run:
        with patch("app.integrations.kubernetes.rest.RestClient") as mock_k8s_client:
            with patch("app.integrations.kubernetes.rest.load_connection") as mock_load_connection:
                # 3. Approve
                approve_payload = {"expectedProposalVersion": "v1", "note": "Safety First"}
                res = client.post(f"/api/recommendations/{recommendation_id}/approve", json=approve_payload)
                assert res.status_code == 200
                assert res.json()["status"] == "APPROVED"

                # 4. Assert Kubernetes was NOT interacted with: no kubeconfig read, no connection opened
                mock_k8s_client.assert_not_called()
                mock_load_connection.assert_not_called()

        # Ensure subprocess was never called (e.g. no `kubectl apply`)
        mock_subprocess_run.assert_not_called()
