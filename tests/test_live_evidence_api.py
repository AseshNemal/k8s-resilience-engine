"""HTTP contract for live evidence: explicit errors, valid empty states, and no sample/demo routes."""

import pytest
from fastapi.testclient import TestClient

from app.engine.deployment_topology_modeling import service as topology_service
from app.integrations.kubernetes.client import (
    AuthFailedError,
    ConnectionFailedError,
    ForbiddenError,
    NotConnectedError,
)
from app.main import app
from tests.support.fake_cluster import FakeClusterSource, node, two_node_shop

client = TestClient(app)

LIVE_TOPOLOGY_ROUTES = [
    "/api/deployments/live",
    "/api/deployments/discovery",
    "/api/deployments/topology/nodes",
    "/api/deployments/topology/nodes/worker-1",
    "/api/deployments/topology/zones",
    "/api/deployments/topology/services",
    "/api/deployments/topology/services/web",
    "/api/deployments/drift",
    "/api/deployments/drift/violations",
    "/api/deployments/drift/categories",
    "/api/deployments/drift/spofs",
    "/api/deployments/drift/concentration",
    "/api/deployments/drift/nodes/risk",
    "/api/deployments/drift/exposure/web",
    "/api/deployments/baseline-b2",
]


@pytest.fixture(autouse=True)
def fresh_snapshots():
    topology_service.clear_cluster_snapshots()
    yield
    topology_service.clear_cluster_snapshots()


def body(response):
    return response.json()["detail"]


# ---- failures are explicit ---------------------------------------------------------------------------

@pytest.mark.parametrize("route", LIVE_TOPOLOGY_ROUTES)
def test_with_no_cluster_every_live_route_says_not_connected(route):
    # No fake installed: the real source runs against the sandboxed, empty kubeconfig location.
    response = client.get(route, params={"namespace": "shop"})

    assert response.status_code == 503
    assert body(response)["code"] == "CLUSTER_NOT_CONNECTED"
    assert body(response)["component"] == "deployment_topology"


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (ConnectionFailedError("down"), "CLUSTER_CONNECTION_FAILED"),
        (AuthFailedError("expired"), "CLUSTER_AUTH_FAILED"),
        (ForbiddenError("list", "pods"), "CLUSTER_FORBIDDEN"),
        (RuntimeError("secret-token-xyz"), "DISCOVERY_FAILED"),
    ],
)
def test_failures_map_to_stable_codes_and_never_leak(use_cluster, failure, code):
    use_cluster(FakeClusterSource(error=failure))

    response = client.get("/api/deployments/live", params={"namespace": "shop"})

    assert response.status_code == 502
    assert body(response)["code"] == code
    assert "secret-token-xyz" not in response.text


def test_failed_discovery_never_contains_a_topology(use_cluster):
    use_cluster(FakeClusterSource(error=ConnectionFailedError("down")))

    payload = client.get("/api/deployments/live").json()

    assert "nodes" not in payload and "services" not in payload


def test_invalid_namespace_is_a_client_error(use_cluster):
    use_cluster(two_node_shop())

    response = client.get("/api/deployments/live", params={"namespace": "Not Valid"})

    assert response.status_code == 422
    assert body(response)["code"] == "INVALID_NAMESPACE"


# ---- successes -----------------------------------------------------------------------------------------

def test_live_returns_the_unchanged_graph_contract(use_cluster):
    use_cluster(two_node_shop())

    payload = client.get("/api/deployments/live", params={"namespace": "shop"}).json()

    assert {"snapshotId", "timestamp", "cluster", "validation", "nodes", "services"} <= payload.keys()
    assert payload["validation"]["servicePodMappingAccuracy"] is None
    assert {s["name"] for s in payload["services"]} == {"web", "db"}


def test_discovery_wraps_the_graph_with_status_and_provenance(use_cluster):
    use_cluster(two_node_shop())

    payload = client.get("/api/deployments/discovery", params={"namespace": "shop"}).json()

    assert payload["status"] == "ok"
    assert payload["namespace"] == "shop"
    assert payload["cluster"] == {"connectionFingerprint": "fake-connection", "identityVerified": False}
    assert payload["declaredState"] == "unavailable"
    assert payload["capturedAt"].endswith("Z")
    assert payload["graph"]["services"]


def test_connected_cluster_with_no_workloads_is_a_valid_empty_state(use_cluster):
    use_cluster(FakeClusterSource(nodes=[node("worker-1")]))

    response = client.get("/api/deployments/discovery", params={"namespace": "empty"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "empty"
    assert payload["graph"]["services"] == []
    assert "NAMESPACE_EXISTENCE_UNVERIFIED" in [w["code"] for w in payload["warnings"]]


def test_partial_discovery_lists_what_could_not_be_resolved(use_cluster):
    source = two_node_shop()
    from tests.support.fake_cluster import pod
    source.pods["shop"].append(pod("stray-1", "worker-1", app="stray"))
    use_cluster(source)

    payload = client.get("/api/deployments/discovery", params={"namespace": "shop"}).json()

    assert payload["status"] == "partial"
    assert payload["unresolved"][0]["reason"] == "NO_MATCHING_DEPLOYMENT"


# ---- declared state ------------------------------------------------------------------------------------

@pytest.mark.parametrize("route", [
    "/api/deployments/manifests",
    "/api/deployments/manifests/services",
    "/api/deployments/manifests/services/web",
    "/api/deployments/manifests/compliance",
])
def test_declared_state_views_report_insufficient_evidence(use_cluster, route):
    use_cluster(two_node_shop())

    response = client.get(route, params={"namespace": "shop"})

    assert response.status_code == 409
    assert body(response)["code"] == "DECLARED_STATE_UNAVAILABLE"


def test_drift_reports_unknown_compliance_not_perfect_compliance(use_cluster):
    use_cluster(two_node_shop())

    payload = client.get("/api/deployments/drift", params={"namespace": "shop"}).json()

    assert payload["constraint_compliance_rate"] is None
    assert payload["declared_state"] == "unavailable"
    assert payload["total_services"] == 2


# ---- snapshots ----------------------------------------------------------------------------------------

def test_snapshot_round_trip_is_labelled_historical(use_cluster):
    use_cluster(two_node_shop())

    frozen = client.post("/api/deployments/snapshots/freeze",
                         params={"snapshot_id": "a", "namespace": "shop"}).json()
    stored = client.get("/api/deployments/snapshots/a/MANUAL").json()

    assert frozen["provenance"]["historical"] is False
    assert stored["provenance"]["historical"] is True
    assert stored["provenance"]["identityVerified"] is False


def test_snapshots_are_refused_when_no_connection_can_vouch_for_them():
    response = client.get("/api/deployments/snapshots")

    assert response.status_code == 409
    assert body(response)["code"] == "SNAPSHOT_PROVENANCE_UNVERIFIED"


# ---- sample / demo / synthetic routes are gone from the product ------------------------------------------

REMOVED_ROUTES = [
    ("GET", "/api/workflows/sample"),
    ("GET", "/api/workflows/benchmarks"),
    ("GET", "/api/workflows/benchmark/checkout"),
    ("GET", "/api/workflows/evaluation/accuracy"),
    ("GET", "/api/workflows/evaluation/sensitivity"),
    ("POST", "/api/workflows/execution-batches/benchmarks/checkout"),
    ("POST", "/api/workflows/jaeger/seed"),
    ("GET", "/api/deployments/sample"),
    ("GET", "/api/deployments/chaos/scenarios"),
    ("GET", "/api/deployments/chaos/scenarios/07_node_drain_eviction"),
    ("POST", "/api/deployments/chaos/run/07_node_drain_eviction"),
    ("GET", "/api/deployments/chaos/matrix/07_node_drain_eviction"),
    ("GET", "/api/deployments/chaos/benchmarks"),
    ("GET", "/api/deployments/benchmarks/accuracy"),
    ("GET", "/api/deployments/benchmarks/report/markdown"),
    ("GET", "/api/deployments/benchmarks/report/json"),
    ("GET", "/api/deployments/benchmarks/latex/07_node_drain_eviction"),
    ("GET", "/api/deployments/benchmarks/ensemble"),
    ("GET", "/api/deployments/benchmarks/ensemble/markdown"),
    ("GET", "/api/failure-impacts/sample"),
    ("GET", "/api/failure-impacts/demo-deployment"),
    ("GET", "/api/failure-impacts/simulate"),
    ("GET", "/api/recommendations/sample"),
    ("GET", "/api/validations/sample"),
]


@pytest.mark.parametrize(("method", "path"), REMOVED_ROUTES)
def test_removed_routes_are_not_served(method, path):
    assert client.request(method, path).status_code in (404, 405)


def test_openapi_advertises_no_sample_demo_or_benchmark_paths():
    paths = client.get("/openapi.json").json()["paths"]
    offenders = [p for p in paths if any(word in p for word in ("sample", "demo", "benchmark", "simulate", "chaos/run", "chaos/matrix", "chaos/scenarios", "/seed"))]
    assert offenders == []
    assert not [p for p in paths if p.startswith("/api/validations")]


def test_kept_live_and_user_supplied_routes_still_exist():
    paths = client.get("/openapi.json").json()["paths"]
    for kept in ("/api/deployments/live", "/api/deployments/discovery", "/api/deployments/diff",
                 "/api/deployments/snapshots/freeze", "/api/deployments/chaos/safety/status",
                 "/api/failure-impacts/runtime", "/api/workflows/live/evidence", "/api/health"):
        assert kept in paths, kept
