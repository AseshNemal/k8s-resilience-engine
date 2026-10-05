"""Generic workflow probe contract tests."""

import pytest

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.engine.workflow_dependency_modeling.workflow_probe_models import (
    WorkflowProbeDefinition,
)


def _probe() -> dict:
    return {
        "runnerType": "http_sequence",
        "initialVariables": {"sku": "item-42"},
        "steps": [
            {
                "name": "create_cart",
                "method": "POST",
                "path": "/api/carts",
                "expectedStatusCodes": [201],
                "headers": {"Accept": "application/json"},
                "secretHeaders": {"Authorization": "SHOP_API_TOKEN"},
                "jsonBody": {"sku": "{{sku}}"},
                "extract": {
                    "cart_id": {"source": "json_field", "selector": "cart.id"}
                },
            },
            {
                "name": "place_order",
                "method": "POST",
                "path": "/api/orders",
                "expectedStatusCodes": [200, 201],
                "jsonBody": {"cartId": "{{cart_id}}"},
                "responseContains": ["orderId"],
            },
        ],
        "successCondition": {
            "protocol": "http",
            "terminalService": "order-api",
            "operationName": "POST /api/orders",
            "httpStatusCodes": [200, 201],
            "requiredAttributes": [],
        },
    }


def test_generic_http_probe_round_trips_without_benchmark_assumptions() -> None:
    probe = WorkflowProbeDefinition.from_mapping(_probe())
    assert probe.steps[0].path == "/api/carts"
    assert probe.steps[0].secret_headers == {"Authorization": "SHOP_API_TOKEN"}
    assert probe.steps[1].response_contains == ("orderId",)
    assert WorkflowProbeDefinition.from_mapping(probe.to_mapping()) == probe


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda data: data["steps"][0].update(path="https://evil.invalid"), "relative"),
        (lambda data: data["steps"][0].update(headers={"Authorization": "raw"}), "Sensitive"),
        (lambda data: data["steps"][0].update(secretHeaders={"Authorization": "bad-name"}), "environment-variable"),
        (lambda data: data["steps"][0].update(jsonBody={}, formBody={"x": "y"}), "cannot combine"),
    ],
)
def test_probe_rejects_unsafe_or_ambiguous_requests(mutator, message: str) -> None:
    payload = _probe()
    mutator(payload)
    with pytest.raises(ComponentDataError, match=message):
        WorkflowProbeDefinition.from_mapping(payload)


def test_probe_supports_grpc_success_contract_independent_of_http_runner() -> None:
    payload = _probe()
    payload["successCondition"] = {
        "protocol": "grpc",
        "terminalService": "payment-api",
        "operationName": "payments.Payment/Charge",
        "grpcStatusCodes": ["OK"],
        "requiredAttributes": [],
    }
    probe = WorkflowProbeDefinition.from_mapping(payload)
    assert probe.success_condition.protocol.value == "grpc"
