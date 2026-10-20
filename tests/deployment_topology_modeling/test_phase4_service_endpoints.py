import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

from app.api.deployments import router as deployments_router
from app.engine.deployment_topology_modeling.service import (
    run_chaos_benchmark_pipeline,
    run_single_chaos_scenario,
)
from experiments.placement_drift.run_benchmarks import run_benchmarks


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployments_router)
    return TestClient(app)


def test_service_run_chaos_benchmark_pipeline():
    payload = run_chaos_benchmark_pipeline()
    assert "aggregate_metrics" in payload
    assert "scenario_results" in payload
    assert len(payload["scenario_results"]) == 8
    assert payload["aggregate_metrics"]["total_scenarios"] == 8.0
    assert payload["aggregate_metrics"]["completed_count"] == 8.0


def test_service_run_single_chaos_scenario():
    result = run_single_chaos_scenario("02_scale_up_stress")
    assert result is not None
    assert result.scenario_id == "02_scale_up_stress"
    assert result.status.value == "COMPLETED"

    unknown = run_single_chaos_scenario("unknown_scenario")
    assert unknown is None


# =====================================================================
# Topic 1: Live State & Topology
# =====================================================================

def test_api_get_topology_nodes(client: TestClient):
    response = client.get("/api/deployments/topology/nodes")
    assert response.status_code == 200
    nodes = response.json()
    assert len(nodes) >= 1
    assert "name" in nodes[0]
    assert "zone" in nodes[0]


def test_api_get_topology_services(client: TestClient):
    response = client.get("/api/deployments/topology/services")
    assert response.status_code == 200
    services = response.json()
    assert len(services) >= 1
    assert "replica_concentration" in services[0]


def test_api_get_service_topology_detail(client: TestClient):
    # Test valid service
    response = client.get("/api/deployments/topology/services/paymentservice")
    assert response.status_code == 200
    data = response.json()
    assert data["service_name"] == "paymentservice"
    assert "pods" in data

    # Test not found
    err_resp = client.get("/api/deployments/topology/services/non_existent_service")
    assert err_resp.status_code == 404


# =====================================================================
# Topic 2: Declared Manifest Intent
# =====================================================================

def test_api_get_manifests(client: TestClient):
    response = client.get("/api/deployments/manifests")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "service_name" in data[0]


def test_api_get_manifest_compliance(client: TestClient):
    response = client.get("/api/deployments/manifests/compliance")
    assert response.status_code == 200
    data = response.json()
    assert "constraint_compliance_rate" in data
    assert "total_services" in data


# =====================================================================
# Topic 3: Resilience Drift & Failure Domain Exposure
# =====================================================================

def test_api_get_drift_and_baseline_b2(client: TestClient):
    drift_resp = client.get("/api/deployments/drift")
    assert drift_resp.status_code == 200
    drift_data = drift_resp.json()
    assert "total_services" in drift_data
    assert "exposure_rate" in drift_data

    b2_resp = client.get("/api/deployments/baseline-b2")
    assert b2_resp.status_code == 200
    b2_data = b2_resp.json()
    assert "service_rankings" in b2_data
    assert "total_single_points_of_failure" in b2_data


def test_api_get_drift_violations(client: TestClient):
    response = client.get("/api/deployments/drift/violations")
    assert response.status_code == 200
    violations = response.json()
    assert isinstance(violations, list)


def test_api_get_service_exposure_detail(client: TestClient):
    # Test valid service
    response = client.get("/api/deployments/drift/exposure/paymentservice")
    assert response.status_code == 200
    data = response.json()
    assert data["service_name"] == "paymentservice"
    assert "exposure_level" in data

    # Test not found
    err_resp = client.get("/api/deployments/drift/exposure/non_existent_service")
    assert err_resp.status_code == 404


# =====================================================================
# Topic 4: Chaos Engineering (Disruption Scenarios)
# =====================================================================

def test_api_get_chaos_scenarios_list(client: TestClient):
    response = client.get("/api/deployments/chaos/scenarios")
    assert response.status_code == 200
    scenarios = response.json()
    assert len(scenarios) == 8
    scenario_ids = [s["scenario_id"] for s in scenarios]
    assert "01_initial_deployment" in scenario_ids
    assert "07_node_drain_eviction" in scenario_ids


def test_api_get_chaos_scenario_detail(client: TestClient):
    response = client.get("/api/deployments/chaos/scenarios/07_node_drain_eviction")
    assert response.status_code == 200
    data = response.json()
    assert data["scenario_id"] == "07_node_drain_eviction"
    assert "target_service" in data

    err_resp = client.get("/api/deployments/chaos/scenarios/non_existent")
    assert err_resp.status_code == 404


def test_api_post_run_chaos_scenario_success(client: TestClient):
    response = client.post("/api/deployments/chaos/run/05_pod_deletion_chaos")
    assert response.status_code == 200
    data = response.json()
    assert data["scenario_id"] == "05_pod_deletion_chaos"
    assert data["status"] == "COMPLETED"
    assert data["d_rate"] > 0.0


def test_api_post_run_chaos_scenario_not_found(client: TestClient):
    response = client.post("/api/deployments/chaos/run/non_existent_id")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_api_get_chaos_matrix(client: TestClient):
    response = client.get("/api/deployments/chaos/matrix/07_node_drain_eviction")
    assert response.status_code == 200
    data = response.json()
    assert data["scenario_id"] == "07_node_drain_eviction"
    assert "nodes" in data
    assert "transition_matrix" in data
    assert "ascii_table" in data
    assert "latex_table" in data
    assert len(data["nodes"]) >= 3


def test_api_get_chaos_matrix_not_found(client: TestClient):
    response = client.get("/api/deployments/chaos/matrix/invalid_scenario")
    assert response.status_code == 404


# =====================================================================
# Topic 5: Academic Benchmarks & Validation
# =====================================================================

def test_api_get_chaos_benchmarks(client: TestClient):
    response = client.get("/api/deployments/chaos/benchmarks")
    assert response.status_code == 200
    data = response.json()
    assert "aggregate_metrics" in data
    assert "scenario_results" in data
    assert len(data["scenario_results"]) == 8
    assert data["aggregate_metrics"]["completed_count"] == 8.0


def test_api_get_academic_accuracy_certificate(client: TestClient):
    response = client.get("/api/deployments/benchmarks/accuracy")
    assert response.status_code == 200
    data = response.json()
    assert "service_pod_mapping_accuracy" in data
    assert "concentration_detection_accuracy" in data
    assert data["service_pod_mapping_accuracy"] >= 0.98


def test_api_get_markdown_benchmark_report(client: TestClient):
    response = client.get("/api/deployments/benchmarks/report/markdown")
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    assert "# Component 2" in response.text


def test_run_benchmarks_script_execution():
    metrics = run_benchmarks()
    assert metrics["total_scenarios"] == 8.0
    assert metrics["completed_count"] == 8.0

    output_dir = Path(__file__).resolve().parents[2] / "experiments" / "placement_drift"
    md_file = output_dir / "benchmark_report.md"
    json_file = output_dir / "benchmark_report.json"

    assert md_file.exists()
    assert json_file.exists()
    assert md_file.stat().st_size > 0
    assert json_file.stat().st_size > 0


# =====================================================================
# Additional Topic 1 Tests: Node Detail & Zones
# =====================================================================

def test_api_get_node_topology_detail(client: TestClient):
    # Retrieve node list first
    nodes_resp = client.get("/api/deployments/topology/nodes")
    assert nodes_resp.status_code == 200
    first_node = nodes_resp.json()[0]["name"]

    resp = client.get(f"/api/deployments/topology/nodes/{first_node}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_name"] == first_node
    assert "total_pods" in data
    assert "services" in data

    err_resp = client.get("/api/deployments/topology/nodes/non_existent_node")
    assert err_resp.status_code == 404


def test_api_get_topology_zones(client: TestClient):
    resp = client.get("/api/deployments/topology/zones")
    assert resp.status_code == 200
    zones = resp.json()
    assert isinstance(zones, list)
    assert len(zones) >= 1
    assert "zone" in zones[0]
    assert "node_count" in zones[0]


# =====================================================================
# Additional Topic 2 Tests: Manifest Service Detail
# =====================================================================

def test_api_get_manifest_service_detail(client: TestClient):
    resp = client.get("/api/deployments/manifests/services/paymentservice")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service_name"] == "paymentservice"

    err_resp = client.get("/api/deployments/manifests/services/non_existent_service")
    assert err_resp.status_code == 404


# =====================================================================
# Additional Topic 3 Tests: Drift Categories, SPOFs, Concentration & Risk
# =====================================================================

def test_api_get_drift_categories(client: TestClient):
    resp = client.get("/api/deployments/drift/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "scale_drift" in data["summary"]
    assert "topology_spread_violations" in data["summary"]


def test_api_get_drift_spofs(client: TestClient):
    resp = client.get("/api/deployments/drift/spofs")
    assert resp.status_code == 200
    spofs = resp.json()
    assert isinstance(spofs, list)


def test_api_get_drift_concentration(client: TestClient):
    resp = client.get("/api/deployments/drift/concentration")
    assert resp.status_code == 200
    ranked = resp.json()
    assert isinstance(ranked, list)
    assert len(ranked) >= 1
    assert "replica_concentration" in ranked[0]
    # Verify sorted descending
    if len(ranked) > 1:
        assert ranked[0]["replica_concentration"] >= ranked[-1]["replica_concentration"]


def test_api_get_drift_nodes_risk(client: TestClient):
    resp = client.get("/api/deployments/drift/nodes/risk")
    assert resp.status_code == 200
    nodes = resp.json()
    assert isinstance(nodes, list)
    assert len(nodes) >= 1
    # Stored KinD snapshot: all 3 paymentservice pods on resilience-research-worker.
    assert nodes[0]["node_name"] == "resilience-research-worker"
    assert nodes[0]["exposed_services"] == ["paymentservice"]
    assert nodes[0]["risk_level"] == "CRITICAL"
    assert all(n["exposed_service_count"] == 0 and n["risk_level"] == "BALANCED" for n in nodes[1:])


# =====================================================================
# Additional Topic 4 Tests: Safety Guards
# =====================================================================

def test_api_get_chaos_safety_status(client: TestClient):
    resp = client.get("/api/deployments/chaos/safety/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["safety_guard_enforced"] is True
    assert "kube-system" in data["protected_namespaces"]


def test_api_post_chaos_safety_validate(client: TestClient):
    resp = client.post("/api/deployments/chaos/safety/validate?namespace=default")
    assert resp.status_code == 200
    data = resp.json()
    assert "action_allowed" in data


# =====================================================================
# Additional Topic 5 Tests: Snapshots & Provenance
# =====================================================================

def test_api_snapshots_lifecycle(client: TestClient):
    # 1. Freeze a snapshot
    freeze_resp = client.post("/api/deployments/snapshots/freeze?snapshot_id=test_freeze")
    assert freeze_resp.status_code == 200
    snap = freeze_resp.json()
    assert snap["snapshot_id"] == "test_freeze"
    assert "sha256_hash" in snap

    # 2. List snapshots
    list_resp = client.get("/api/deployments/snapshots")
    assert list_resp.status_code == 200
    keys = list_resp.json()
    assert "test_freeze_MANUAL" in keys

    # 3. Get snapshot detail
    detail_resp = client.get("/api/deployments/snapshots/test_freeze/MANUAL")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["snapshot_id"] == "test_freeze"

    # 4. Clear snapshots
    clear_resp = client.delete("/api/deployments/snapshots")
    assert clear_resp.status_code == 200
    assert "cleared" in clear_resp.json()["message"].lower()


# =====================================================================
# Additional Topic 6 Tests: Publications & LaTeX
# =====================================================================

def test_api_get_json_benchmark_report(client: TestClient):
    resp = client.get("/api/deployments/benchmarks/report/json")
    assert resp.status_code == 200
    data = resp.json()
    assert "aggregate_metrics" in data
    assert "scenario_results" in data


def test_api_get_latex_transition_table(client: TestClient):
    resp = client.get("/api/deployments/benchmarks/latex/07_node_drain_eviction")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario_id"] == "07_node_drain_eviction"
    assert "\\begin{tabular}" in data["latex_table"]
    assert "d_rate" in data

    err_resp = client.get("/api/deployments/benchmarks/latex/invalid_scenario")
    assert err_resp.status_code == 404

