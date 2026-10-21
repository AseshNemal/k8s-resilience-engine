"""Tests for the generic, trace-correlated HTTP workflow runner."""

from datetime import datetime, timezone

import httpx
import pytest

from app.engine.workflow_dependency_modeling.workflow_probe_models import (
    WorkflowProbeDefinition,
)
from app.engine.workflow_dependency_modeling.workflow_probe_runner import (
    approved_application_url,
    run_http_workflow_probe,
)


TRACE_ID = "0123456789abcdef0123456789abcdef"


def _definition() -> WorkflowProbeDefinition:
    return WorkflowProbeDefinition.from_mapping(
        {
            "runnerType": "http_sequence",
            "initialVariables": {"sku": "item-42"},
            "steps": [
                {
                    "name": "create_cart",
                    "method": "POST",
                    "path": "/api/carts",
                    "expectedStatusCodes": [201],
                    "secretHeaders": {"Authorization": "SHOP_API_TOKEN"},
                    "jsonBody": {"sku": "{{sku}}"},
                    "extract": {
                        "cart_id": {
                            "source": "json_field",
                            "selector": "cart.id",
                        }
                    },
                },
                {
                    "name": "place_order",
                    "method": "POST",
                    "path": "/api/orders",
                    "expectedStatusCodes": [201],
                    "headers": {"X-Cart": "{{cart_id}}"},
                    "jsonBody": {"cartId": "{{cart_id}}"},
                    "responseContains": ["confirmed"],
                },
            ],
            "successCondition": {
                "protocol": "http",
                "terminalService": "order-api",
                "operationName": "POST /api/orders",
                "httpStatusCodes": [201],
                "requiredAttributes": [],
            },
        }
    )


def test_traces_each_step_with_separate_trace_roots() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/carts":
            return httpx.Response(201, json={"cart": {"id": "cart-7"}})
        return httpx.Response(201, text="order confirmed")

    result = run_http_workflow_probe(
        "http://localhost:9000",
        _definition(),
        trace_id=TRACE_ID,
        transport=httpx.MockTransport(handler),
        started_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
        secrets={"SHOP_API_TOKEN": "Bearer test-secret"},
    )

    assert result.business_success is True
    assert [request.url.path for request in requests] == [
        "/api/carts",
        "/api/orders",
    ]
    assert requests[0].headers["traceparent"].startswith("00-")
    assert TRACE_ID not in requests[0].headers["traceparent"]
    assert requests[1].headers["traceparent"].startswith(f"00-{TRACE_ID}-")
    assert result.trace_id == TRACE_ID
    assert requests[0].headers["authorization"] == "Bearer test-secret"
    assert requests[1].headers["x-cart"] == "cart-7"
    assert requests[1].content == b'{"cartId":"cart-7"}'


def test_explicit_measured_step_controls_success_trace_id() -> None:
    payload = _definition().to_mapping()
    payload["measuredStep"] = "create_cart"
    definition = WorkflowProbeDefinition.from_mapping(payload)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/carts":
            return httpx.Response(201, json={"cart": {"id": "cart-7"}})
        return httpx.Response(201, text="order confirmed")

    run_http_workflow_probe(
        "http://localhost:9000",
        definition,
        trace_id=TRACE_ID,
        transport=httpx.MockTransport(handler),
        secrets={"SHOP_API_TOKEN": "secret"},
    )

    assert requests[0].headers["traceparent"].startswith(f"00-{TRACE_ID}-")
    assert requests[1].headers["traceparent"].startswith("00-")
    assert TRACE_ID not in requests[1].headers["traceparent"]


def test_rejects_unknown_measured_step() -> None:
    payload = _definition().to_mapping()
    payload["measuredStep"] = "missing_step"

    with pytest.raises(ValueError, match="measuredStep"):
        WorkflowProbeDefinition.from_mapping(payload)


def test_stops_after_first_failed_assertion() -> None:
    called_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called_paths.append(request.url.path)
        return httpx.Response(503, text="unavailable")

    result = run_http_workflow_probe(
        "http://127.0.0.1:9000",
        _definition(),
        trace_id=TRACE_ID,
        transport=httpx.MockTransport(handler),
        secrets={"SHOP_API_TOKEN": "secret"},
    )

    assert result.business_success is False
    assert called_paths == ["/api/carts"]
    assert "HTTP 503" in result.reason
    assert result.steps[0].succeeded is False


def test_early_failure_reports_the_trace_id_used_for_failed_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, text="unavailable")

    result = run_http_workflow_probe(
        "http://127.0.0.1:9000",
        _definition(),
        trace_id=TRACE_ID,
        transport=httpx.MockTransport(handler),
        secrets={"SHOP_API_TOKEN": "secret"},
    )

    assert result.business_success is False
    assert result.trace_id != TRACE_ID
    assert requests[0].headers["traceparent"].startswith(
        f"00-{result.trace_id}-"
    )


def test_rejects_missing_secret_before_network_access() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201)

    definition = _definition()
    transport = httpx.MockTransport(handler)
    with pytest.raises(ValueError, match="SHOP_API_TOKEN"):
        run_http_workflow_probe(
            "http://localhost:9000",
            definition,
            transport=transport,
            secrets={},
        )
    assert called is False


def test_rejects_unapproved_target_before_network_access() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201)

    definition = _definition()
    transport = httpx.MockTransport(handler)
    with pytest.raises(ValueError, match="not approved"):
        run_http_workflow_probe(
            "http://169.254.169.254/latest/meta-data",
            definition,
            transport=transport,
            secrets={"SHOP_API_TOKEN": "secret"},
        )
    assert called is False


def test_explicit_adapter_allowlist_does_not_change_process_configuration() -> None:
    assert (
        approved_application_url(
            "https://reviewed-app.internal/root",
            allowed_hosts={"reviewed-app.internal"},
        )
        == "https://reviewed-app.internal/root"
    )


def test_missing_extracted_value_is_an_evidence_failure() -> None:
    result = run_http_workflow_probe(
        "http://localhost:9000",
        _definition(),
        trace_id=TRACE_ID,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(201, json={"cart": {}})
        ),
        secrets={"SHOP_API_TOKEN": "secret"},
    )
    assert result.business_success is False
    assert "cart.id" in result.reason
