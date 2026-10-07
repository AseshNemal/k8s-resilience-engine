"""Tests for multi-workflow benchmark datasets and endpoints."""

import pytest
from fastapi.testclient import TestClient

from app.engine.workflow_dependency_modeling.service import (
    get_workflow_by_id,
    list_benchmark_workflows,
)
from app.engine.workflow_dependency_modeling.fixture_repository import get_reference
from app.main import app

client = TestClient(app)


def test_list_benchmark_workflows():
    benchmarks = list_benchmark_workflows()
    ids = [b["workflowId"] for b in benchmarks]
    assert "checkout" in ids
    assert "view_product" in ids
    assert "add_to_cart" in ids
    assert "refund" in ids
    assert all(item["synthetic"] is True for item in benchmarks)
    assert all(item["referenceAvailable"] is True for item in benchmarks)
    assert all(item["sourceKind"] == "synthetic_fixture" for item in benchmarks)


def test_get_view_product_workflow():
    workflow = get_workflow_by_id("view_product")
    assert workflow.workflow_id == "view_product"
    assert "productcatalogservice" in workflow.mandatory_services
    assert "currencyservice" in workflow.mandatory_services
    assert "recommendationservice" in workflow.optional_services or "adservice" in workflow.optional_services
    assert len(workflow.edges) >= 3


def test_get_add_to_cart_workflow():
    workflow = get_workflow_by_id("add_to_cart")
    assert workflow.workflow_id == "add_to_cart"
    assert "cartservice" in workflow.mandatory_services
    assert "productcatalogservice" in workflow.mandatory_services
    assert "currencyservice" in workflow.mandatory_services


def test_get_workflow_by_id_not_found():
    with pytest.raises(FileNotFoundError):
        get_workflow_by_id("non_existent_workflow_xyz")


@pytest.mark.parametrize(
    ("workflow_id", "mandatory", "optional"),
    [
        (
            "checkout",
            {"frontend", "cartservice", "checkoutservice", "paymentservice"},
            {"emailservice"},
        ),
        (
            "view_product",
            {"frontend", "productcatalogservice", "currencyservice"},
            {"recommendationservice", "adservice"},
        ),
        (
            "add_to_cart",
            {"frontend", "cartservice", "productcatalogservice", "currencyservice"},
            set(),
        ),
        (
            "refund",
            {"frontend", "orderservice", "paymentservice"},
            {"notificationservice"},
        ),
    ],
)
def test_benchmark_reference_classification_labels(
    workflow_id: str, mandatory: set[str], optional: set[str]
) -> None:
    reference = get_reference(workflow_id)

    assert set(reference.mandatory_services) == mandatory
    assert set(reference.optional_services) == optional
    assert set(reference.expected_services) == mandatory | optional


def test_api_get_benchmarks():
    response = client.get("/api/workflows/benchmarks")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 4
    ids = [w["workflowId"] for w in data]
    assert "checkout" in ids
    assert "view_product" in ids


def test_api_get_benchmark_specific():
    response = client.get("/api/workflows/benchmark/view_product")
    assert response.status_code == 200
    data = response.json()
    assert data["workflowId"] == "view_product"
    assert "productcatalogservice" in data["mandatoryServices"]


def test_api_get_benchmark_404():
    response = client.get("/api/workflows/benchmark/unknown_workflow")
    assert response.status_code == 404
