"""Tests for workflow evaluation and sensitivity analysis endpoints."""

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_evaluate_workflow_accuracy_checkout():
    response = client.get("/api/workflows/evaluation/accuracy?workflow_id=checkout&mandatory_threshold=1.0")
    assert response.status_code == 200
    data = response.json()
    assert data["workflowId"] == "checkout"
    assert data["serviceMetrics"]["precision"] == 1.0
    assert data["serviceMetrics"]["recall"] == 1.0
    assert data["serviceMetrics"]["f1"] == 1.0
    assert data["mandatoryServiceMetrics"]["f1"] == 1.0
    assert data["discoveryServiceMetrics"]["f1"] == 1.0
    assert data["edgeMetrics"]["accuracy"] == 1.0
    assert data["edgeMetrics"]["jaccardSimilarity"] == 1.0
    assert data["pathMetrics"]["f1"] == 1.0
    assert data["workflowReconstructionAccuracy"] == 1.0
    assert data["graphProcessingTimeMs"] >= 0.0
    assert data["mandatoryEdgeMetrics"] is None
    assert (
        data["mandatoryEdgeEvaluationStatus"]
        == "not_evaluated_no_predicted_edge_labels"
    )


def test_evaluate_workflow_accuracy_view_product():
    response = client.get("/api/workflows/evaluation/accuracy?workflow_id=view_product&mandatory_threshold=1.0")
    assert response.status_code == 200
    data = response.json()
    assert data["workflowId"] == "view_product"
    assert data["serviceMetrics"]["precision"] >= 0.8
    assert data["serviceMetrics"]["recall"] >= 0.8


def test_evaluate_workflow_accuracy_not_found():
    response = client.get("/api/workflows/evaluation/accuracy?workflow_id=non_existent_123")
    assert response.status_code == 404


def test_get_sensitivity_analysis():
    response = client.get("/api/workflows/evaluation/sensitivity?workflow_id=checkout")
    assert response.status_code == 200
    steps = response.json()
    assert len(steps) >= 5
    # First step should be threshold 0.5, last should be 1.0
    assert steps[0]["threshold"] == 0.5
    assert steps[-1]["threshold"] == 1.0
    for s in steps:
        assert "precision" in s
        assert "recall" in s
        assert "f1" in s
        assert "edgeAccuracy" in s


def test_accuracy_distinguishes_discovery_from_mandatory_classification():
    response = client.get(
        "/api/workflows/evaluation/accuracy?workflow_id=checkout&mandatory_threshold=0.5"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["discoveryServiceMetrics"]["recall"] == pytest.approx(1.0)
    assert data["mandatoryServiceMetrics"]["precision"] < 1.0


@pytest.mark.parametrize("threshold", [-0.01, 1.01])
def test_evaluation_rejects_out_of_range_threshold(threshold):
    response = client.get(
        f"/api/workflows/evaluation/accuracy?workflow_id=checkout&mandatory_threshold={threshold}"
    )
    assert response.status_code == 422
