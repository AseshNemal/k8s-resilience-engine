from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import failure_impacts as routes
from app.engine.failure_impact_prediction.deployment_impact import (
    predict_deployment_failure_impact,
)
from app.main import app
from app.shared.schemas.deployment import (
    DeploymentGraph,
    NodeModel,
    PodModel,
    ServicePlacement,
)


def graph() -> DeploymentGraph:
    return DeploymentGraph(
        nodes=[NodeModel(name="worker-a"), NodeModel(name="worker-b")],
        services=[
            ServicePlacement(
                name="orders",
                declared_replicas=2,
                ready_replicas=2,
                pods=[
                    PodModel(name="orders-a", node="worker-a", ready=True),
                    PodModel(name="orders-b", node="worker-a", ready=True),
                ],
            ),
            ServicePlacement(
                name="catalog",
                declared_replicas=2,
                ready_replicas=2,
                pods=[
                    PodModel(name="catalog-a", node="worker-a", ready=True),
                    PodModel(name="catalog-b", node="worker-b", ready=True),
                ],
            ),
            ServicePlacement(
                name="reports",
                declared_replicas=1,
                ready_replicas=0,
                pods=[PodModel(name="reports-a", node="worker-b", ready=False)],
            ),
        ],
    )


def test_deployment_prediction_reports_service_outcomes_without_workflow_metrics():
    report = predict_deployment_failure_impact(graph(), "worker-a")

    assert report.failed_services == ["orders"]
    assert report.affected_services == ["orders", "catalog"]
    assert report.already_unavailable_services == ["reports"]
    assert report.service_failure_rate == pytest.approx(50.0)
    outcomes = {impact.service: impact for impact in report.service_impacts}
    assert outcomes["orders"].outcome == "failed"
    assert outcomes["catalog"].outcome == "degraded"
    assert outcomes["catalog"].ready_after == 1
    assert outcomes["reports"].outcome == "already_unavailable"
    assert report.workflow_evidence_status == "not_used"
    assert not hasattr(report, "blast_radius")
    assert not hasattr(report, "failed_workflows")


def test_deployment_node_ranking_uses_service_exposure_not_workflow_blast_radius():
    report = predict_deployment_failure_impact(graph(), "worker-b")

    assert report.failed_services == []
    assert report.affected_services == ["catalog"]
    assert report.node_risk_ranking[0].node == "worker-a"
    assert report.node_risk_ranking[0].failed_services == ["orders"]
    assert report.node_risk_ranking[0].service_failure_rate == pytest.approx(50.0)


def test_deployment_runtime_route_returns_camel_case_contract(monkeypatch):
    report = predict_deployment_failure_impact(graph(), "worker-a")
    discovery = SimpleNamespace(
        status="ok", captured_at="2026-01-01T00:00:00Z", warnings=[], unresolved=[]
    )
    monkeypatch.setattr(
        routes,
        "predict_runtime_deployment_failure_impact_with_evidence",
        lambda failed_node, namespace: (report, discovery),
    )

    response = TestClient(app).get(
        "/api/failure-impacts/deployment/runtime",
        params={"failed_node": "worker-a", "namespace": "shop"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["analysisScope"] == "deployment"
    assert body["serviceFailureRate"] == pytest.approx(50.0)
    assert body["serviceImpacts"][0]["readyBefore"] == 2
    assert "blastRadius" not in body
    assert response.headers["X-Topology-Status"] == "ok"
