"""
Tests for Phase 5 Academic Validation Panel.
Component: Component 2 - Deployment Topology Modeling
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_academic_panel_rendered_in_html(client):
    response = client.get("/components/deployments")
    assert response.status_code == 200
    html = response.text

    assert 'id="academic-panel"' in html
    assert "MODEL VALIDATION (kubectl ground truth)" in html
    assert "Self-chosen target" in html
    assert "Model Accuracy &amp; Runtime Metrics" in html or "Model Accuracy & Runtime Metrics" in html
    assert 'id="stat-svc-pod-acc"' in html
    assert 'id="stat-pod-node-acc"' in html
    assert 'id="stat-owner-acc"' in html
    assert 'id="stat-conc-acc"' in html
    assert 'id="stat-latency-overhead"' in html
    assert 'id="btn-copy-accuracy-json"' in html


def test_accuracy_endpoint_returns_valid_certificate(client):
    response = client.get("/api/deployments/benchmarks/accuracy")
    assert response.status_code == 200
    data = response.json()

    assert "service_pod_mapping_accuracy" in data
    assert "pod_node_mapping_accuracy" in data
    assert "owner_resolution_accuracy" in data
    assert "concentration_detection_accuracy" in data
    assert "is_statistically_sound" in data

    # Measured against kubectl output of the stored KinD snapshot (3 pods, 1 service, 1 node failure).
    assert data["service_pod_mapping_accuracy"] == pytest.approx(1.0)
    assert data["pod_node_mapping_accuracy"] == pytest.approx(1.0)
    assert data["exposure_detection_accuracy"] == pytest.approx(1.0)
    # The snapshot holds no ReplicaSet objects, so owner resolution is not scored...
    assert data["owner_resolution_accuracy"] is None
    # ...and one tiny snapshot is never reported as statistically sound.
    assert data["is_statistically_sound"] is False


def test_ground_truth_endpoint_reports_sample_sizes(client):
    data = client.get("/api/deployments/benchmarks/ground-truth").json()
    assert data["pods_evaluated"] == 3
    assert data["services_evaluated"] == 1
    assert data["failure_experiments"] == 1
    (check,) = data["exposure_checks"]
    assert check["failed_nodes"] == ["resilience-research-worker"]
    assert check["predicted_available"] is False and check["observed_available"] is False
