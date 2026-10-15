"""
Tests for Multi-Service Parametric Ensemble Evaluation.
Component: Component 2 - Deployment Topology Modeling
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from experiments.placement_drift.parametric_ensemble_evaluator import (
    ParametricEnsembleEvaluator,
    EnsembleBenchmarkMetrics,
)
from app.engine.deployment_topology_modeling.service import (
    run_parametric_ensemble_pipeline,
    get_parametric_ensemble_markdown_report,
)


@pytest.fixture
def client():
    return TestClient(app)


def test_parametric_ensemble_execution():
    evaluator = ParametricEnsembleEvaluator()
    metrics = evaluator.run_ensemble()

    assert isinstance(metrics, EnsembleBenchmarkMetrics)
    assert metrics.total_trials == 12
    assert metrics.successful_trials == 12
    assert 0.0 <= metrics.mean_d_rate <= 1.0
    assert metrics.std_dev_d_rate > 0.0
    assert metrics.min_d_rate <= metrics.mean_d_rate <= metrics.max_d_rate
    assert len(metrics.confidence_interval_95) == 2
    assert metrics.confidence_interval_95[0] <= metrics.confidence_interval_95[1]
    assert len(metrics.trials) == 12

    # Check key services are present
    assert "cartservice" in metrics.per_service_drift
    assert "paymentservice" in metrics.per_service_drift
    assert "frontend" in metrics.per_service_drift


def test_parametric_ensemble_markdown_report():
    evaluator = ParametricEnsembleEvaluator()
    metrics = evaluator.run_ensemble()
    report = ParametricEnsembleEvaluator.generate_markdown_report(metrics)

    assert "# Multi-Service Parametric Ensemble Evaluation" in report
    assert "Sample Mean Placement Drift Rate" in report
    assert "95% Confidence Interval" in report
    assert "| `cartservice` |" in report
    assert "| `paymentservice` |" in report


def test_service_parametric_ensemble():
    res = run_parametric_ensemble_pipeline()
    assert "mean_d_rate" in res
    assert "std_dev_d_rate" in res
    assert "confidence_interval_95" in res
    assert res["total_trials"] == 12

    md = get_parametric_ensemble_markdown_report()
    assert isinstance(md, str)
    assert len(md) > 200


def test_api_get_ensemble_endpoints(client):
    res = client.get("/api/deployments/benchmarks/ensemble")
    assert res.status_code == 200
    data = res.json()
    assert data["total_trials"] == 12
    assert "mean_d_rate" in data
    assert "confidence_interval_95" in data

    res_md = client.get("/api/deployments/benchmarks/ensemble/markdown")
    assert res_md.status_code == 200
    assert "text/markdown" in res_md.headers.get("content-type", "")
    assert "# Multi-Service Parametric Ensemble Evaluation" in res_md.text
