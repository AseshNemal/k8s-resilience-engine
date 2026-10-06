"""Tests for evidence-based workflow probe onboarding."""

import pytest

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.engine.workflow_dependency_modeling.workflow_onboarding import (
    build_workflow_probe,
)


def test_observed_http_entry_becomes_executable_without_internal_fields() -> None:
    definition = build_workflow_probe(
        "order-api",
        "POST /orders",
        {
            "mode": "observed_entry",
            "expectedStatusCodes": [201, 202],
            "request": {
                "secretHeaders": {"Authorization": "ORDER_API_TOKEN"},
                "jsonBody": {"sku": "item-42", "quantity": 1},
                "responseContains": ["orderId"],
            },
        },
    )

    assert definition.steps[0].method == "POST"
    assert definition.steps[0].path == "/orders"
    assert definition.success_condition.terminal_service == "order-api"
    assert definition.success_condition.http_status_codes == (201, 202)


def test_observed_route_parameters_require_explicit_values() -> None:
    with pytest.raises(ComponentDataError, match="product_id"):
        build_workflow_probe(
            "catalog-api",
            "GET /products/{product_id}",
            {"mode": "observed_entry", "expectedStatusCodes": [200]},
        )

    definition = build_workflow_probe(
        "catalog-api",
        "GET /products/{product_id}",
        {
            "mode": "observed_entry",
            "expectedStatusCodes": [200],
            "request": {"pathVariables": {"product_id": "sku-9"}},
        },
    )
    assert definition.steps[0].path == "/products/{{product_id}}"
    assert definition.initial_variables == {"product_id": "sku-9"}


def test_non_http_observation_requires_reviewed_import() -> None:
    with pytest.raises(ComponentDataError, match="not an executable HTTP"):
        build_workflow_probe(
            "consumer",
            "orders.created process",
            {"mode": "observed_entry", "expectedStatusCodes": [200]},
        )


def test_imported_multistep_definition_uses_same_contract() -> None:
    definition = build_workflow_probe(
        "gateway",
        "POST /checkout",
        {
            "mode": "imported_definition",
            "executionDefinition": {
                "runnerType": "http_sequence",
                "steps": [
                    {
                        "name": "login",
                        "method": "POST",
                        "path": "/sessions",
                        "expectedStatusCodes": [201],
                    },
                    {
                        "name": "checkout",
                        "method": "POST",
                        "path": "/checkout",
                        "expectedStatusCodes": [200],
                    },
                ],
                "successCondition": {
                    "protocol": "span",
                    "terminalService": "payment-worker",
                    "operationName": "capture-payment",
                    "spanStatusCodes": ["OK"],
                    "requiredAttributes": [],
                },
            },
        },
    )
    assert [step.name for step in definition.steps] == ["login", "checkout"]
    assert definition.success_condition.protocol.value == "span"


def test_unknown_setup_fields_are_rejected_instead_of_ignored() -> None:
    with pytest.raises(ComponentDataError, match="unsupported field"):
        build_workflow_probe(
            "order-api",
            "POST /orders",
            {
                "mode": "observed_entry",
                "expectedStatusCodes": [201],
                "magicSuccess": True,
            },
        )
