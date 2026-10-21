"""
Tests for Phase 5 Modals: Failure Domain Drill-Down & Snapshot Diff Modals.
Component: Component 2 - Deployment Topology Modeling
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_modals_markup_presence_in_deployments(client):
    response = client.get("/components/deployments")
    assert response.status_code == 200
    html = response.text

    # Modal 1: Failure Domain Drill-Down
    assert 'id="modal-violation-backdrop"' in html
    assert 'id="modal-violation-title"' in html
    assert 'id="btn-close-violation-modal"' in html
    assert 'id="violation-service-name"' in html
    assert 'id="violation-exposure-level"' in html
    assert 'id="violation-rules-list"' in html

    # The dry-run snapshot diff modal was removed from the dashboard (the /diff API remains).
    assert 'id="modal-diff-backdrop"' not in html


def test_snapshot_diff_api_endpoint(client):
    payload = {
        "baseline": {
            "timestamp": "T0",
            "total_services": 1,
            "services": [
                {
                    "service_name": "payment",
                    "total_replicas": 2,
                    "ready_replicas": 2,
                    "replica_concentration": 0.5,
                    "exposure_status": "NOT_EXPOSED",
                    "exposure_level": "RESILIENT",
                    "exposed_failure_domains": [],
                }
            ],
        },
        "target": {
            "timestamp": "T1",
            "total_services": 1,
            "services": [
                {
                    "service_name": "payment",
                    "total_replicas": 2,
                    "ready_replicas": 2,
                    "replica_concentration": 1.0,
                    "exposure_status": "EXPOSED",
                    "exposure_level": "FULL_EXPOSURE",
                    "exposed_failure_domains": ["worker-1"],
                }
            ],
        },
    }
    response = client.post("/api/deployments/diff", json=payload)
    assert response.status_code == 200
    diff_data = response.json()

    assert "placement_drift_rate" in diff_data
    assert "total_services_compared" in diff_data
    assert "drifted_services_count" in diff_data
    assert diff_data["total_services_compared"] == 1
    assert diff_data["drifted_services_count"] == 1
    assert diff_data["newly_exposed_services"] == ["payment"]


def test_snapshot_diff_rejects_reports_without_exposure_status(client):
    legacy = {"services": [{"service_name": "payment", "exposure_level": "RESILIENT"}]}
    response = client.post("/api/deployments/diff", json={"baseline": legacy, "target": legacy})
    assert response.status_code == 422


