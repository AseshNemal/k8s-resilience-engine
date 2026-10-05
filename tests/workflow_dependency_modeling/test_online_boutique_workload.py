"""Tests for the real Online Boutique checkout workload protocol."""

from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from app.engine.workflow_dependency_modeling.online_boutique_workload import (
    online_boutique_checkout_probe,
    run_online_boutique_checkout,
)


TRACE_ID = "0123456789abcdef0123456789abcdef"


def test_benchmark_is_a_generic_probe_profile() -> None:
    definition = online_boutique_checkout_probe("OLJCESPC7Z", 2028)
    assert [step.name for step in definition.steps] == [
        "view_product",
        "add_to_cart",
        "checkout",
    ]
    assert definition.steps[-1].response_contains == ("Your order is complete!",)


def test_executes_reviewed_checkout_flow_with_exact_trace_context() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.startswith("/product/"):
            return httpx.Response(200, text="Product")
        if request.url.path == "/cart":
            return httpx.Response(302, headers={"location": "/cart"})
        return httpx.Response(200, text="<h3>Your order is complete!</h3>")

    result = run_online_boutique_checkout(
        "http://localhost:8080",
        trace_id=TRACE_ID,
        transport=httpx.MockTransport(handler),
        started_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    assert result.business_success is True
    assert [request.url.path for request in requests] == [
        "/product/OLJCESPC7Z",
        "/cart",
        "/cart/checkout",
    ]
    assert requests[-1].headers["traceparent"].startswith(f"00-{TRACE_ID}-")
    assert parse_qs(requests[1].content.decode())["product_id"] == ["OLJCESPC7Z"]
    checkout = parse_qs(requests[-1].content.decode())
    assert checkout["email"] == ["workflow-research@example.invalid"]
    assert result.to_mapping()["steps"]["checkoutStatus"] == 200


def test_checkout_without_confirmation_marker_is_not_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cart":
            return httpx.Response(302)
        return httpx.Response(200, text="No confirmed order")

    result = run_online_boutique_checkout(
        "http://127.0.0.1:8080",
        trace_id=TRACE_ID,
        transport=httpx.MockTransport(handler),
    )
    assert result.business_success is False
    assert "required marker" in result.reason


def test_rejects_unapproved_frontend_before_network_access() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    with pytest.raises(ValueError, match="not approved"):
        run_online_boutique_checkout(
            "http://169.254.169.254/latest/meta-data",
            transport=httpx.MockTransport(handler),
        )
    assert called is False


def test_rejects_product_outside_reviewed_catalog() -> None:
    with pytest.raises(ValueError, match="reviewed Online Boutique catalog"):
        run_online_boutique_checkout(
            "http://localhost:8080",
            product_id="attacker-controlled-product",
        )
