"""Deterministic Online Boutique v0.10.1 checkout workload driver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit
import httpx

from app.engine.workflow_dependency_modeling.workflow_probe_models import (
    WorkflowProbeDefinition,
)
from app.engine.workflow_dependency_modeling.workflow_probe_runner import (
    run_http_workflow_probe,
)


ONLINE_BOUTIQUE_PRODUCTS = frozenset(
    {
        "0PUK6V6EV0",
        "1YMWWN1N4O",
        "2ZYFJ3GM2N",
        "66VCHSJNUP",
        "6E92ZMYYFZ",
        "9SIQT8TOJO",
        "L9ECAV7KIM",
        "LS4PSXUNUM",
        "OLJCESPC7Z",
    }
)
DEFAULT_PRODUCT_ID = "OLJCESPC7Z"
_ORDER_MARKER = "Your order is complete!"


@dataclass(frozen=True)
class OnlineBoutiqueRunResult:
    trace_id: str
    product_id: str
    started_at: datetime
    finished_at: datetime
    product_status: int
    cart_status: int
    checkout_status: int
    business_success: bool
    reason: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "productId": self.product_id,
            "startedAt": self.started_at.isoformat().replace("+00:00", "Z"),
            "finishedAt": self.finished_at.isoformat().replace("+00:00", "Z"),
            "steps": {
                "productStatus": self.product_status,
                "cartStatus": self.cart_status,
                "checkoutStatus": self.checkout_status,
            },
            "businessSuccess": self.business_success,
            "reason": self.reason,
        }


def approved_boutique_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Online Boutique frontend URL is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Online Boutique frontend URL must be absolute HTTP or HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Online Boutique frontend URL cannot contain credentials, query, or fragment")
    allowed = {
        item.strip().lower()
        for item in os.getenv(
            "WORKFLOW_BOUTIQUE_ALLOWED_HOSTS",
            "localhost,127.0.0.1,::1,frontend,boutique-frontend",
        ).split(",")
        if item.strip()
    }
    if parsed.hostname.lower() not in allowed:
        raise ValueError(
            "Online Boutique host is not approved; configure WORKFLOW_BOUTIQUE_ALLOWED_HOSTS"
        )
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Workload timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def online_boutique_checkout_probe(
    product_id: str,
    expiration_year: int,
) -> WorkflowProbeDefinition:
    """Return the benchmark-specific data consumed by the generic runner."""
    if product_id not in ONLINE_BOUTIQUE_PRODUCTS:
        raise ValueError("productId is not part of the reviewed Online Boutique catalog")
    return WorkflowProbeDefinition.from_mapping(
        {
            "runnerType": "http_sequence",
            "measuredStep": "checkout",
            "initialVariables": {
                "product_id": product_id,
                "expiration_year": str(expiration_year),
            },
            "steps": [
                {
                    "name": "view_product",
                    "method": "GET",
                    "path": "/product/{{product_id}}",
                    "expectedStatusCodes": [200],
                },
                {
                    "name": "add_to_cart",
                    "method": "POST",
                    "path": "/cart",
                    "expectedStatusCodes": [302, 303],
                    "formBody": {
                        "product_id": "{{product_id}}",
                        "quantity": "1",
                    },
                },
                {
                    "name": "checkout",
                    "method": "POST",
                    "path": "/cart/checkout",
                    "expectedStatusCodes": [200],
                    "formBody": {
                        "email": "workflow-research@example.invalid",
                        "street_address": "1 Experiment Way",
                        "zip_code": "94016",
                        "city": "Test City",
                        "state": "CA",
                        "country": "United States",
                        "credit_card_number": "4111111111111111",
                        "credit_card_expiration_month": "12",
                        "credit_card_expiration_year": "{{expiration_year}}",
                        "credit_card_cvv": "123",
                    },
                    "responseContains": [_ORDER_MARKER],
                },
            ],
            "successCondition": {
                "protocol": "http",
                "terminalService": "frontend",
                "operationName": "POST /cart/checkout",
                "httpStatusCodes": [200],
                "requiredAttributes": [],
            },
        }
    )


def run_online_boutique_checkout(
    frontend_url: str,
    *,
    product_id: str = DEFAULT_PRODUCT_ID,
    trace_id: str | None = None,
    timeout_seconds: float = 15.0,
    transport: httpx.BaseTransport | None = None,
    started_at: datetime | None = None,
) -> OnlineBoutiqueRunResult:
    """Execute product → cart → checkout and inject an exact checkout trace ID."""
    base_url = approved_boutique_url(frontend_url)
    start = _utc(started_at)
    expiration_year = start.year + 2
    definition = online_boutique_checkout_probe(product_id, expiration_year)
    hostname = urlsplit(base_url).hostname
    if hostname is None:  # pragma: no cover - guaranteed by approved_boutique_url
        raise ValueError("Online Boutique frontend URL requires a hostname")
    run = run_http_workflow_probe(
        base_url,
        definition,
        trace_id=trace_id,
        timeout_seconds=timeout_seconds,
        transport=transport,
        started_at=start,
        allowed_hosts={hostname.lower()},
    )
    statuses = {item.name: item.status_code for item in run.steps}

    return OnlineBoutiqueRunResult(
        trace_id=run.trace_id,
        product_id=product_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        product_status=statuses.get("view_product", 0),
        cart_status=statuses.get("add_to_cart", 0),
        checkout_status=statuses.get("checkout", 0),
        business_success=run.business_success,
        reason=run.reason,
    )
