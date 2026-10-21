import pytest
from fastapi.testclient import TestClient

from tests.support.research_app import app  # research-only routes are not served by app.main


client = TestClient(app)


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/health",
        "/api/workflows/sample",
        "/api/deployments/sample",
        "/api/failure-impacts/sample",
        "/api/recommendations/sample",
        "/api/validations/sample",
    ],
)
def test_sample_endpoint_returns_success(endpoint: str) -> None:
    response = client.get(endpoint)

    assert response.status_code == 200
    assert response.json()