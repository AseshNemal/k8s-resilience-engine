from fastapi.testclient import TestClient

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


def test_failure_impact_page_contains_node_simulation_controls() -> None:
    response = client.get("/components/failure-impacts")

    assert response.status_code == 200
    assert 'data-role="node-selector"' in response.text
    assert "Simulate Node Failure" in response.text
