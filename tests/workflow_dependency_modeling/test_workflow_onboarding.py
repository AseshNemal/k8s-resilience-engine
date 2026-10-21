"""Tests for evidence-based workflow probe onboarding."""

import pytest
import httpx

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.engine.workflow_dependency_modeling.workflow_onboarding import (
    build_workflow_probe,
    workflow_setup_requirements,
)
from app.engine.workflow_dependency_modeling.workflow_probe_runner import run_http_workflow_probe


def _guided(**changes):
    return {"mode": "guided_sequence", "successStatuses": "201", "actionInputs": {}, **changes}


def test_guided_form_compiles_and_runs_with_shared_cookies_and_captured_values():
    setup = _guided(
        actionInputs={"pathParameters": [{"name": "id", "value": "{{orderId}}"}],
                      "bodyFormat": "json", "bodyFields": [
                          {"name": "quantity", "value": "2", "type": "number"},
                          {"name": "notify", "value": "false", "type": "boolean"}],
                      "responseText": "accepted"},
        preparationSteps=[{
            "operation": "POST /orders", "successStatuses": "201",
            "inputs": {"bodyFormat": "form", "bodyFields": [{"name": "sku", "value": "chosen-item"}]},
            "captures": [{"name": "orderId", "source": "json_field", "selector": "id"}],
        }],
    )
    definition = build_workflow_probe("gateway", "POST /orders/{id}/confirm", setup)
    assert definition.measured_step == "workflow_action"
    assert definition.success_condition.operation_name == "POST /orders/{id}/confirm"
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/orders":
            assert request.content == b"sku=chosen-item"
            return httpx.Response(201, json={"id": "order-7"}, headers={"set-cookie": "session=abc; Path=/"})
        assert request.url.path == "/orders/order-7/confirm"
        assert request.headers["cookie"] == "session=abc"
        assert request.content == b'{"quantity":2,"notify":false}'
        return httpx.Response(201, text="accepted")

    result = run_http_workflow_probe("http://localhost:8080", definition, transport=httpx.MockTransport(respond))
    assert result.business_success
    assert len(requests) == 2
    assert requests[0].headers["traceparent"] != requests[1].headers["traceparent"]
    assert requests[1].headers["traceparent"].split("-")[1] == result.trace_id


def test_guided_literal_route_values_are_encoded_per_step_without_collisions():
    definition = build_workflow_probe("gateway", "GET /items/{id}", _guided(
        successStatuses="200, 200", actionInputs={"pathParameters": [{"name": "id", "value": "a/b"}]},
        preparationSteps=[{"operation": "GET /groups/{id}", "successStatuses": "200", "inputs": {"pathParameters": [{"name": "id", "value": "different"}]}}],
    ))
    assert definition.steps[0].path == "/groups/different"
    assert definition.steps[1].path == "/items/a%2Fb"
    assert definition.initial_variables == {}
    assert definition.steps[-1].expected_status_codes == (200,)


@pytest.mark.parametrize("changes,match", [
    ({"successStatuses": "200,broken"}, "success codes"),
    ({"successStatuses": "200,"}, "success codes"),
    ({"successStatuses": True}, "success codes"),
    ({"actionInputs": {"headers": [{"name": "Authorization", "value": "literal-token"}]}}, "Sensitive headers"),
    ({"actionInputs": {"headers": [{"name": "X-Mode", "value": "a"}, {"name": "x-mode", "value": "b"}]}}, "duplicate"),
    ({"actionInputs": {"queryParameters": [{"name": "", "value": "orphan"}]}}, "enter a name"),
    ({"actionInputs": {"queryParameters": [{"name": "id", "value": "{{missing}}"}]}}, "not available"),
    ({"actionInputs": {"bodyFormat": "none", "bodyFields": [{"name": "id", "value": "a"}]}}, "select form or JSON"),
    ({"actionInputs": {"bodyFormat": "json", "bodyFields": [{"name": "n", "type": "number", "value": "NaN"}]}}, "non-finite"),
    ({"actionInputs": {"bodyFormat": "json", "bodyFields": [{"name": "n", "type": "number", "value": "true"}]}}, "must be a number"),
    ({"preparationSteps": [{"operation": "GET https://example.com/", "successStatuses": "200"}]}, "application-relative"),
    ({"preparationSteps": [{}] * 20}, "at most 19"),
    ({"actionInputs": {"mandatoryServices": ["injected"]}}, "unsupported"),
    ({"executionDefinition": {}}, "unsupported"),
])
def test_guided_setup_rejects_invalid_or_ambiguous_inputs(changes, match):
    with pytest.raises(ComponentDataError, match=match):
        build_workflow_probe("gateway", "POST /orders", _guided(**changes))


def test_guided_setup_requires_real_path_values_and_does_not_infer_internal_calls():
    with pytest.raises(ComponentDataError, match="path parameters"):
        build_workflow_probe("gateway", "GET /items/{id}", _guided())
    with pytest.raises(ComponentDataError, match="non-empty"):
        build_workflow_probe("gateway", "GET /items/{id}", _guided(
            actionInputs={"pathParameters": [{"name": "id", "value": ""}]},
        ))
    with pytest.raises(ComponentDataError, match="HTTP method"):
        build_workflow_probe("worker", "orders.process", _guided())
    assert workflow_setup_requirements("orders.process")["guidedSupported"] is False
    assert workflow_setup_requirements("GET /items/{sku}")["action"]["pathParameters"] == ["sku"]


def test_guided_response_capture_cannot_redirect_requests_to_another_host():
    definition = build_workflow_probe("gateway", "GET /{{nextPath}}", _guided(
        successStatuses="200", preparationSteps=[{
            "operation": "GET /session", "successStatuses": "200",
            "captures": [{"name": "nextPath", "source": "json_field", "selector": "path"}],
        }],
    ))
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"path": "/external.example/steal"})
    with pytest.raises(ValueError, match="remain relative"):
        run_http_workflow_probe("http://localhost:8080", definition, transport=httpx.MockTransport(respond))
    assert len(requests) == 1


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
