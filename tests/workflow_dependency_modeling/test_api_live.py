"""Tests for Member 1 live Jaeger API endpoints."""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from tests.support.research_app import app  # research-only routes are not served by app.main

client = TestClient(app)


def test_get_sample_workflow():
    response = client.get("/api/workflows/sample")
    assert response.status_code == 200
    data = response.json()
    assert data["workflowId"] == "checkout"
    assert "paymentservice" in data["mandatoryServices"]
    assert "edges" in data


@patch("app.integrations.jaeger.client.JaegerClient.is_healthy")
def test_get_jaeger_status(mock_is_healthy):
    mock_is_healthy.return_value = True
    response = client.get("/api/workflows/jaeger/status")
    assert response.status_code == 200
    data = response.json()
    assert data["healthy"] is True


@patch("app.integrations.jaeger.client.JaegerClient.get_services")
def test_get_jaeger_services(mock_get_services):
    mock_get_services.return_value = ["frontend", "cartservice", "paymentservice"]
    response = client.get("/api/workflows/jaeger/services")
    assert response.status_code == 200
    assert response.json() == ["frontend", "cartservice", "paymentservice"]


@patch("app.integrations.jaeger.client.JaegerClient.get_services")
def test_get_jaeger_services_error(mock_get_services):
    mock_get_services.side_effect = ConnectionError("Jaeger offline")
    response = client.get("/api/workflows/jaeger/services")
    assert response.status_code == 503


@patch("app.api.workflows.discover_live_workflows")
def test_discover_workflows_api(mock_discover):
    mock_discover.return_value = [
        {
            "workflowId": "checkout",
            "operationName": "POST /cart/checkout",
            "rootService": "frontend",
            "traceCount": 10,
            "participatingServices": ["frontend", "cartservice", "paymentservice"],
            "sampleTraceIds": ["t1"],
        }
    ]
    response = client.get("/api/workflows/discover?service_name=frontend")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["workflowId"] == "checkout"


@patch("app.api.workflows.get_live_workflow")
def test_read_live_workflow_api(mock_get_live):
    from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph

    mock_get_live.return_value = WorkflowGraph(
        workflow_id="checkout",
        criticality=0.9,
        mandatory_services=["cartservice", "paymentservice"],
        optional_services=["emailservice"],
        edges=[WorkflowEdge(source="cartservice", target="paymentservice")],
    )
    response = client.get("/api/workflows/live?service_name=frontend&operation=checkout")
    assert response.status_code == 200
    data = response.json()
    assert data["workflowId"] == "checkout"
    assert data["mandatoryServices"] == ["cartservice", "paymentservice"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/workflows/discover?limit=0",
        "/api/workflows/discover?limit=5001",
        "/api/workflows/live?criticality=1.1",
        "/api/workflows/live?mandatory_threshold=-0.1",
    ],
)
def test_live_routes_reject_out_of_range_parameters(path):
    assert client.get(path).status_code == 422
