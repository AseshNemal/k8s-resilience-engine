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
    assert "THESIS BENCHMARK CERTIFICATE" in html
    assert "Empirical Modeling Accuracy & Rigor" in html
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

    assert data["service_pod_mapping_accuracy"] >= 0.95
    assert data["pod_node_mapping_accuracy"] >= 0.95
    assert data["concentration_detection_accuracy"] >= 0.95
    assert data["is_statistically_sound"] is True
