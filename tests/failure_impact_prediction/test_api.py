import pytest
from fastapi import HTTPException
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
    assert "/api/failure-impacts/portfolio/simulate" in paths
    assert "/api/failure-impacts/portfolio/runtime" in paths


def test_demo_portfolio_route_returns_public_contract() -> None:
    report = failure_impact_routes.simulate_demo_portfolio_failure("node-1")
    payload = report.model_dump(by_alias=True)

    assert payload["failedNode"] == "node-1"
    assert payload["totalWorkflows"] == 2
    assert payload["failedWorkflows"] == ["checkout"]
    assert payload["survivingWorkflows"] == ["storefront"]
    assert payload["blastRadius"] == 50.0
    assert payload["nodeRiskRanking"][0] == {
        "node": "node-1",
        "blastRadius": 50.0,
        "failedWorkflows": ["checkout"],
    }


def test_demo_portfolio_route_rejects_unknown_node() -> None:
    with pytest.raises(HTTPException) as caught:
        failure_impact_routes.simulate_demo_portfolio_failure("node-99")

    assert caught.value.status_code == 422
    assert "Unknown failed node" in caught.value.detail


def test_runtime_portfolio_route_forwards_selection(monkeypatch) -> None:
    prediction = failure_impact_routes.predict_demo_portfolio_failure_impact(
        "node-1"
    )
    calls: dict[str, object] = {}

    def runtime_prediction(
        workflow_ids: tuple[str, ...],
        failed_node: str,
        namespace: str,
    ):
        calls.update(
            workflow_ids=workflow_ids,
            failed_node=failed_node,
            namespace=namespace,
        )
        return prediction

    monkeypatch.setattr(
        failure_impact_routes,
        "predict_runtime_portfolio_failure_impact",
        runtime_prediction,
    )

    report = failure_impact_routes.simulate_runtime_portfolio_failure(
        ["checkout", "storefront"],
        "node-1",
        "research",
    )

    assert calls == {
        "workflow_ids": ("checkout", "storefront"),
        "failed_node": "node-1",
        "namespace": "research",
    }
    assert report.total_workflows == 2


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (LookupError("Missing frozen workflow"), 404),
        (ValueError("Unknown failed node"), 422),
        (
            RuntimeInputUnavailableError("Component 2 is unavailable"),
            503,
        ),
    ],
)
def test_runtime_portfolio_route_maps_errors(
    monkeypatch,
    error: Exception,
    status_code: int,
) -> None:
    def failing_prediction(*args, **kwargs):
        raise error

    monkeypatch.setattr(
        failure_impact_routes,
        "predict_runtime_portfolio_failure_impact",
        failing_prediction,
    )

    with pytest.raises(HTTPException) as caught:
        failure_impact_routes.simulate_runtime_portfolio_failure(
            ["checkout", "storefront"],
            "node-1",
            "default",
        )

    assert caught.value.status_code == status_code


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
