from fastapi.testclient import TestClient

from app.api import failure_impacts as failure_impact_routes
from app.engine.failure_impact_prediction.service import (
    RuntimeInputUnavailableError,
    get_sample_failure_impact,
)
from app.main import app


client = TestClient(app)


def test_simulation_endpoint_returns_calculated_report() -> None:
    response = client.get(
        "/api/failure-impacts/simulate",
        params={"failed_node": "node-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["failedNode"] == "node-1"
    assert payload["failedServices"] == ["cartservice"]
    assert payload["failedWorkflows"] == ["checkout"]
    assert payload["blastRadius"] == 100.0


def test_simulation_endpoint_rejects_unknown_node() -> None:
    response = client.get(
        "/api/failure-impacts/simulate",
        params={"failed_node": "node-99"},
    )

    assert response.status_code == 422
    assert "Unknown failed node" in response.json()["detail"]


def test_demo_deployment_is_explicitly_identified() -> None:
    response = client.get("/api/failure-impacts/demo-deployment")

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshotId"] == "component3-demo-snapshot-v1"
    assert {service["name"] for service in payload["services"]} >= {
        "cartservice",
        "checkoutservice",
        "paymentservice",
    }


def test_component_three_routes_are_registered() -> None:
    paths = app.openapi()["paths"]

    assert "/api/failure-impacts/simulate" in paths
    assert "/api/failure-impacts/demo-deployment" in paths
    assert "/api/failure-impacts/runtime" in paths


def test_runtime_endpoint_forwards_validated_input_selection(monkeypatch) -> None:
    calls: dict[str, str] = {}

    def runtime_prediction(
        workflow_id: str,
        failed_node: str,
        namespace: str,
    ):
        calls.update(
            workflow_id=workflow_id,
            failed_node=failed_node,
            namespace=namespace,
        )
        return get_sample_failure_impact()

    monkeypatch.setattr(
        failure_impact_routes,
        "predict_runtime_failure_impact",
        runtime_prediction,
    )

    response = client.get(
        "/api/failure-impacts/runtime",
        params={
            "workflow_id": "checkout",
            "failed_node": "node-1",
            "namespace": "research",
        },
    )

    assert response.status_code == 200
    assert calls == {
        "workflow_id": "checkout",
        "failed_node": "node-1",
        "namespace": "research",
    }


def test_runtime_endpoint_reports_missing_frozen_workflow(monkeypatch) -> None:
    def missing_workflow(*args, **kwargs):
        raise LookupError("No frozen workflow graph for checkout")

    monkeypatch.setattr(
        failure_impact_routes,
        "predict_runtime_failure_impact",
        missing_workflow,
    )

    response = client.get(
        "/api/failure-impacts/runtime",
        params={"workflow_id": "checkout", "failed_node": "node-1"},
    )

    assert response.status_code == 404
    assert "No frozen workflow graph" in response.json()["detail"]


def test_runtime_endpoint_reports_incompatible_topology(monkeypatch) -> None:
    def incomplete_topology(*args, **kwargs):
        raise ValueError("Mandatory workflow services have no deployment topology")

    monkeypatch.setattr(
        failure_impact_routes,
        "predict_runtime_failure_impact",
        incomplete_topology,
    )

    response = client.get(
        "/api/failure-impacts/runtime",
        params={"workflow_id": "checkout", "failed_node": "node-1"},
    )

    assert response.status_code == 422
    assert "no deployment topology" in response.json()["detail"]


def test_runtime_endpoint_reports_unavailable_component_input(monkeypatch) -> None:
    def unavailable_input(*args, **kwargs):
        raise RuntimeInputUnavailableError(
            "Component 1 frozen-workflow storage is unavailable"
        )

    monkeypatch.setattr(
        failure_impact_routes,
        "predict_runtime_failure_impact",
        unavailable_input,
    )

    response = client.get(
        "/api/failure-impacts/runtime",
        params={"workflow_id": "checkout", "failed_node": "node-1"},
    )

    assert response.status_code == 503
    assert "storage is unavailable" in response.json()["detail"]


def test_failure_impact_page_contains_node_simulation_controls() -> None:
    response = client.get("/components/failure-impacts")

    assert response.status_code == 200
    assert 'data-role="input-mode"' in response.text
    assert 'value="runtime"' in response.text
    assert 'data-role="workflow-id"' in response.text
    assert 'data-role="namespace"' in response.text
    assert 'data-role="node-selector"' in response.text
    assert "Simulate Node Failure" in response.text
