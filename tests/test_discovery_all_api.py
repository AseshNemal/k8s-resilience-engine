"""GET /api/deployments/discovery/all: the Overview's view of every namespace of the connected cluster."""

from fastapi.testclient import TestClient

from app.integrations.kubernetes.client import ForbiddenError
from app.main import app
from tests.support.fake_cluster import FakeClusterSource, multi_namespace_cluster

client = TestClient(app)
ROUTE = "/api/deployments/discovery/all"


def test_returns_a_result_per_namespace_with_totals_and_provenance(use_cluster):
    use_cluster(multi_namespace_cluster())

    response = client.get(ROUTE)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert [n["namespace"] for n in payload["namespaces"]] == ["resilience-test", "shop"]
    assert payload["excludedNamespaces"] == ["kube-system"]
    assert payload["totals"] == {"workerNodes": 2, "services": 3, "unresolved": 0}
    assert payload["cluster"] == {"connectionFingerprint": "fake-connection", "identityVerified": False}
    assert payload["capturedAt"].endswith("Z")
    assert all(n["graph"]["services"] for n in payload["namespaces"])


def test_include_system_adds_the_system_namespaces(use_cluster):
    use_cluster(multi_namespace_cluster())

    payload = client.get(ROUTE, params={"include_system": "true"}).json()

    assert [n["namespace"] for n in payload["namespaces"]] == ["kube-system", "resilience-test", "shop"]
    assert payload["excludedNamespaces"] == []


def test_with_no_cluster_it_says_not_connected():
    response = client.get(ROUTE)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CLUSTER_NOT_CONNECTED"
    assert response.json()["detail"]["component"] == "deployment_topology"


def test_a_forbidden_credential_is_an_explicit_error_not_a_partial_overview(use_cluster):
    use_cluster(FakeClusterSource(error=ForbiddenError("list", "pods")))

    response = client.get(ROUTE)

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "CLUSTER_FORBIDDEN"
    assert "namespaces" not in response.json()


def test_the_single_namespace_discovery_route_is_unchanged(use_cluster):
    use_cluster(multi_namespace_cluster())

    payload = client.get("/api/deployments/discovery", params={"namespace": "shop"}).json()

    assert payload["namespace"] == "shop"
    assert payload["status"] == "ok"
