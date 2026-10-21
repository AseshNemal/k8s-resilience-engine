from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import failure_impacts as failure_impact_routes
from app.engine.deployment_topology_modeling.discovery import DiscoveryError, DiscoveryWarning
from app.engine.failure_impact_prediction.service import (
    RuntimeInputUnavailableError,
    predict_demo_failure_impact,
    predict_demo_portfolio_failure_impact,
)
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.main import app
from app.shared.evidence_errors import unavailable
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph
from tests.support.fake_cluster import FakeClusterSource, two_node_shop

client = TestClient(app)

RUNTIME = "/api/failure-impacts/runtime"
PARAMS = {"workflow_id": "checkout", "failed_node": "worker-1", "namespace": "shop"}


def evidence(**overrides):
    base = dict(topology_status="ok", topology_captured_at="2026-01-01T00:00:00Z",
                topology_warnings=[], topology_unresolved=[])
    base.update(overrides)
    return SimpleNamespace(**base)


def freeze_checkout(monkeypatch):
    graph = WorkflowGraph(workflow_id="checkout", criticality=0.9, mandatory_services=["web", "db"],
                          edges=[WorkflowEdge(source="web", target="db")])
    monkeypatch.setattr(
        workflow_service, "get_frozen_workflow_graph",
        lambda workflow_id: SimpleNamespace(graph=graph, freeze_id="f1", assessment_id="a1"),
    )


# ---- Member 3: fixture, portfolio and route-registration tests ------------------------------------------

def test_component_three_routes_are_registered() -> None:
    paths = app.openapi()["paths"]

    assert "/api/failure-impacts/runtime" in paths
    assert "/api/failure-impacts/portfolio/runtime" in paths
    # The sample, contract-fixture and portfolio-fixture routes are not part of the product.
    for removed in ("/sample", "/demo-deployment", "/simulate", "/portfolio/simulate"):
        assert f"/api/failure-impacts{removed}" not in paths


def test_runtime_portfolio_route_forwards_selection(monkeypatch) -> None:
    prediction = predict_demo_portfolio_failure_impact(
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


# ---- single-workflow runtime route: topology evidence headers and stable error codes ----------------------
# The runtime route returns the topology evidence a prediction was computed from (X-Topology-* headers) and
# refuses with explicit codes instead of predicting on a partly resolved topology.

def test_runtime_endpoint_forwards_validated_input_selection_and_reports_provenance(monkeypatch) -> None:
    calls: dict[str, str] = {}

    def runtime_prediction(workflow_id: str, failed_node: str, namespace: str):
        calls.update(workflow_id=workflow_id, failed_node=failed_node, namespace=namespace)
        return predict_demo_failure_impact("node-1"), evidence(
            topology_status="partial",
            topology_warnings=[DiscoveryWarning(code="NAMESPACE_EXISTENCE_UNVERIFIED", message="m")],
        )

    monkeypatch.setattr(failure_impact_routes, "predict_runtime_failure_impact_with_evidence", runtime_prediction)

    response = client.get(RUNTIME, params={"workflow_id": "checkout", "failed_node": "node-1", "namespace": "research"})

    assert response.status_code == 200
    assert calls == {"workflow_id": "checkout", "failed_node": "node-1", "namespace": "research"}
    assert response.headers["X-Topology-Status"] == "partial"
    assert response.headers["X-Topology-Captured-At"] == "2026-01-01T00:00:00Z"
    assert response.headers["X-Topology-Warnings"] == "NAMESPACE_EXISTENCE_UNVERIFIED"


def test_missing_frozen_workflow_is_a_404_with_a_stable_code(monkeypatch) -> None:
    def missing(*args, **kwargs):
        raise unavailable("WORKFLOW_EVIDENCE_UNAVAILABLE", "No frozen workflow graph for checkout", "workflow",
                          http_status=404)

    monkeypatch.setattr(failure_impact_routes, "predict_runtime_failure_impact_with_evidence", missing)

    response = client.get(RUNTIME, params=PARAMS)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "WORKFLOW_EVIDENCE_UNAVAILABLE"


def test_incompatible_topology_is_a_422(monkeypatch) -> None:
    def incomplete(*args, **kwargs):
        raise ValueError("Mandatory workflow services have no deployment topology")

    monkeypatch.setattr(failure_impact_routes, "predict_runtime_failure_impact_with_evidence", incomplete)

    response = client.get(RUNTIME, params=PARAMS)

    assert response.status_code == 422
    assert "no deployment topology" in response.json()["detail"]


def test_unavailable_component_input_is_a_503(monkeypatch) -> None:
    def unavailable_input(*args, **kwargs):
        raise RuntimeInputUnavailableError("Component 1 frozen-workflow storage is unavailable")

    monkeypatch.setattr(failure_impact_routes, "predict_runtime_failure_impact_with_evidence", unavailable_input)

    response = client.get(RUNTIME, params=PARAMS)

    assert response.status_code == 503
    assert "storage is unavailable" in response.json()["detail"]["message"]


# ---- end to end through the real engine with a fake cluster ---------------------------------------------

def test_prediction_is_computed_from_the_discovered_cluster(monkeypatch, use_cluster) -> None:
    freeze_checkout(monkeypatch)
    use_cluster(two_node_shop())

    response = client.get(RUNTIME, params=PARAMS)

    assert response.status_code == 200
    report = response.json()
    assert report["failedNode"] == "worker-1"
    assert report["failedServices"] == ["db"]  # the single db replica is on worker-1
    assert report["failedWorkflows"] == ["checkout"]
    assert response.headers["X-Topology-Status"] == "ok"
    assert report["failedNode"] != "node-1"  # never the fixture's node names


def test_without_a_cluster_no_prediction_is_produced(monkeypatch) -> None:
    freeze_checkout(monkeypatch)

    response = client.get(RUNTIME, params=PARAMS)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CLUSTER_NOT_CONNECTED"
    assert "failedServices" not in response.json()


@pytest.mark.parametrize("code", ["CLUSTER_CONNECTION_FAILED", "CLUSTER_AUTH_FAILED", "CLUSTER_FORBIDDEN"])
def test_cluster_failures_surface_as_errors_not_reports(monkeypatch, use_cluster, code) -> None:
    from app.integrations.kubernetes.client import AuthFailedError, ConnectionFailedError, ForbiddenError

    freeze_checkout(monkeypatch)
    failure = {"CLUSTER_CONNECTION_FAILED": ConnectionFailedError("x"), "CLUSTER_AUTH_FAILED": AuthFailedError("x"),
               "CLUSTER_FORBIDDEN": ForbiddenError("list", "pods")}[code]
    use_cluster(FakeClusterSource(error=failure))

    response = client.get(RUNTIME, params=PARAMS)

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == code


def test_unscheduled_replica_of_a_workflow_service_blocks_the_prediction(monkeypatch, use_cluster) -> None:
    from tests.support.fake_cluster import deployment, pod

    freeze_checkout(monkeypatch)
    source = two_node_shop()
    source.pods["shop"].append(pod("db-2", None, app="db", ready=False, phase="Pending"))
    source.deployments["shop"] = [deployment("web", 2), deployment("db", 2)]
    use_cluster(source)

    response = client.get(RUNTIME, params=PARAMS)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "TOPOLOGY_INCOMPLETE"
    assert {s["service"]: s["reasons"] for s in detail["details"]["services"]} == {"db": ["UNSCHEDULED_PODS"]}
    assert "failedServices" not in response.json()


def test_unknown_failed_node_is_rejected(monkeypatch, use_cluster) -> None:
    freeze_checkout(monkeypatch)
    use_cluster(two_node_shop())

    response = client.get(RUNTIME, params={**PARAMS, "failed_node": "node-99"})

    assert response.status_code == 422


def test_failure_impact_page_contains_node_simulation_controls() -> None:
    response = client.get("/components/failure-impacts")

    assert response.status_code == 200
    assert 'data-role="input-mode"' in response.text
    assert 'value="portfolio-demo"' not in response.text
    assert 'value="demo"' not in response.text
    assert 'value="runtime"' in response.text
    assert 'value="portfolio-runtime"' in response.text
    assert 'data-role="workflow-id"' in response.text
    assert 'data-role="workflow-id-label"' in response.text
    assert 'data-role="namespace"' in response.text
    assert 'data-role="node-selector"' in response.text
    assert 'data-role="portfolio-workflow-outcomes"' in response.text
    assert "Simulate Node Failure" in response.text
