"""Deterministic Online Boutique v0.10.1 checkout workload driver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx


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
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")


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


def _traceparent(trace_id: str) -> str:
    if not _TRACE_ID.fullmatch(trace_id):
        raise ValueError("traceId must contain exactly 32 lowercase hexadecimal characters")
    return f"00-{trace_id}-{uuid4().hex[:16]}-01"


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
    if product_id not in ONLINE_BOUTIQUE_PRODUCTS:
        raise ValueError("productId is not part of the reviewed Online Boutique catalog")
    if not 1 <= timeout_seconds <= 60:
        raise ValueError("timeoutSeconds must be between 1 and 60")
    resolved_trace_id = trace_id or uuid4().hex
    traceparent = _traceparent(resolved_trace_id)
    start = _utc(started_at)
    expiration_year = start.year + 2

    with httpx.Client(
        base_url=base_url,
        timeout=timeout_seconds,
        follow_redirects=False,
        transport=transport,
    ) as client:
        product = client.get(f"/product/{product_id}")
        cart = client.post(
            "/cart",
            data={"product_id": product_id, "quantity": "1"},
        )
        checkout = client.post(
            "/cart/checkout",
            headers={"traceparent": traceparent},
            data={
                "email": "workflow-research@example.invalid",
                "street_address": "1 Experiment Way",
                "zip_code": "94016",
                "city": "Test City",
                "state": "CA",
                "country": "United States",
                "credit_card_number": "4111111111111111",
                "credit_card_expiration_month": "12",
                "credit_card_expiration_year": str(expiration_year),
                "credit_card_cvv": "123",
            },
        )

    success = (
        product.status_code == 200
        and cart.status_code in {302, 303}
        and checkout.status_code == 200
        and _ORDER_MARKER in checkout.text
    )
    if product.status_code != 200:
        reason = f"Product page returned HTTP {product.status_code}"
    elif cart.status_code not in {302, 303}:
        reason = f"Cart update returned HTTP {cart.status_code}"
    elif checkout.status_code != 200:
        reason = f"Checkout returned HTTP {checkout.status_code}"
    elif _ORDER_MARKER not in checkout.text:
        reason = "Checkout response did not contain the reviewed order confirmation marker"
    else:
        reason = "Checkout completed and returned the reviewed order confirmation marker"

    return OnlineBoutiqueRunResult(
        trace_id=resolved_trace_id,
        product_id=product_id,
        started_at=start,
        finished_at=datetime.now(timezone.utc),
        product_status=product.status_code,
        cart_status=cart.status_code,
        checkout_status=checkout.status_code,
        business_success=success,
        reason=reason,
    )
