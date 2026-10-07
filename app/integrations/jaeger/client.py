"""client.py — Jaeger REST API Integration Client

Provides an HTTP client for querying a running Jaeger query service (REST API)
to retrieve services, operations, and raw distributed traces.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping
import httpx

logger = logging.getLogger(__name__)

DEFAULT_JAEGER_URL = "http://localhost:16686"
SERVICE_NAME_KEY = "service.name"


class JaegerClient:
    """HTTP client for querying Jaeger's HTTP REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_JAEGER_URL,
        timeout_seconds: float = 5.0,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Initialize the Jaeger client.

        Args:
            base_url: Base URL of the Jaeger query service (e.g. http://localhost:16686).
            timeout_seconds: HTTP request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self.headers = dict(headers or {})

    def is_healthy(self) -> bool:
        """Check if the Jaeger server is reachable and responsive."""
        try:
            with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                response = client.get(f"{self.base_url}/api/services")
                return response.status_code == 200
        except Exception as err:
            logger.warning("Jaeger health check failed at %s: %s", self.base_url, err)
            return False

    def get_services(self) -> list[str]:
        """Fetch the list of all instrumented service names recorded in Jaeger.

        Returns:
            List of service name strings.
        """
        try:
            with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                response = client.get(f"{self.base_url}/api/services")
                response.raise_for_status()
                data = response.json()
                return data.get("data", []) or []
        except Exception as err:
            logger.exception("Failed to fetch services from Jaeger at %s", self.base_url)
            raise ConnectionError(
                f"Could not connect to Jaeger at {self.base_url}: {err}"
            ) from err

    def get_operations(self, service_name: str) -> list[str]:
        """Fetch the list of operation names for a given service.

        Args:
            service_name: Name of the microservice (e.g. 'frontend').

        Returns:
            List of operation names (e.g. ['/cart/checkout', 'HTTP GET']).
        """
        try:
            with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                response = client.get(
                    f"{self.base_url}/api/operations",
                    params={"service": service_name},
                )
                response.raise_for_status()
                data = response.json()
                return data.get("data", []) or []
        except Exception as err:
            logger.exception(
                "Failed to fetch operations for service '%s' from Jaeger",
                service_name,
            )
            raise ConnectionError(
                f"Could not fetch operations for '{service_name}' from Jaeger: {err}"
            ) from err

    def fetch_traces(
        self,
        service_name: str,
        operation: str | None = None,
        limit: int = 100,
        lookback: str = "1h",
        tags: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch raw traces for a service from Jaeger's REST API.

        Args:
            service_name: Name of the microservice to query.
            operation: Optional operation name filter.
            limit: Maximum number of traces to return.
            lookback: Lookback duration string (e.g. '1h', '2d').
            tags: Optional key-value tags to filter by.

        Returns:
            List of raw Jaeger trace dicts (each containing 'traceID', 'spans', 'processes').
        """
        params: dict[str, Any] = {
            "service": service_name,
            "limit": limit,
            "lookback": lookback,
        }
        if operation:
            params["operation"] = operation
        if tags:
            params["tags"] = str(tags)

        try:
            with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                response = client.get(
                    f"{self.base_url}/api/traces",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()
                return data.get("data", []) or []
        except Exception as err:
            logger.exception("Failed to fetch traces from Jaeger at %s", self.base_url)
            raise ConnectionError(
                f"Could not fetch traces from Jaeger at {self.base_url}: {err}"
            ) from err

    def seed_demo_traces(self, otlp_url: str = "http://localhost:4318") -> bool:
        """Push realistic demo traces into Jaeger via its OpenTelemetry OTLP receiver.

        Enables immediate live workflow discovery demonstrations without needing
        a full Kubernetes benchmark cluster running.

        Args:
            otlp_url: Jaeger OTLP HTTP receiver base URL (default http://localhost:4318).

        Returns:
            True if traces were successfully accepted by Jaeger.
        """
        import time
        import uuid

        now_nano = int(time.time() * 1e9)
        endpoint = f"{otlp_url.rstrip('/')}/v1/traces"

        # Generate 5 checkout runs and 3 browsing runs
        resource_spans = []

        for i in range(5):
            trace_id = uuid.uuid4().hex
            root_span_id = uuid.uuid4().hex[:16]
            cart_span_id = uuid.uuid4().hex[:16]
            co_span_id = uuid.uuid4().hex[:16]
            pay_span_id = uuid.uuid4().hex[:16]
            email_span_id = uuid.uuid4().hex[:16]

            start_t = now_nano - (i * 60 * 1_000_000_000)

            # Frontend root
            resource_spans.extend([{
                "resource": {"attributes": [{"key": SERVICE_NAME_KEY, "value": {"stringValue": "frontend"}}]},
                "scopeSpans": [{"spans": [{
                    "traceId": trace_id, "spanId": root_span_id,
                    "name": "POST /cart/checkout", "kind": 1,
                    "startTimeUnixNano": str(start_t), "endTimeUnixNano": str(start_t + 250_000_000),
                    "status": {"code": 1}
                }]}]
            },
            # CartService
            {
                "resource": {"attributes": [{"key": SERVICE_NAME_KEY, "value": {"stringValue": "cartservice"}}]},
                "scopeSpans": [{"spans": [{
                    "traceId": trace_id, "spanId": cart_span_id, "parentSpanId": root_span_id,
                    "name": "get_cart", "kind": 2,
                    "startTimeUnixNano": str(start_t + 10_000_000), "endTimeUnixNano": str(start_t + 50_000_000),
                    "status": {"code": 1}
                }]}]
            },
            # CheckoutService
            {
                "resource": {"attributes": [{"key": SERVICE_NAME_KEY, "value": {"stringValue": "checkoutservice"}}]},
                "scopeSpans": [{"spans": [{
                    "traceId": trace_id, "spanId": co_span_id, "parentSpanId": cart_span_id,
                    "name": "place_order", "kind": 2,
                    "startTimeUnixNano": str(start_t + 60_000_000), "endTimeUnixNano": str(start_t + 220_000_000),
                    "status": {"code": 1}
                }]}]
            },
            # PaymentService (Mandatory: present in all 5 runs)
            {
                "resource": {"attributes": [{"key": SERVICE_NAME_KEY, "value": {"stringValue": "paymentservice"}}]},
                "scopeSpans": [{"spans": [{
                    "traceId": trace_id, "spanId": pay_span_id, "parentSpanId": co_span_id,
                    "name": "charge_credit_card", "kind": 2,
                    "startTimeUnixNano": str(start_t + 80_000_000), "endTimeUnixNano": str(start_t + 180_000_000),
                    "status": {"code": 1}
                }]}]
            }])
            # EmailService (Optional: present only in run 0 and 1)
            if i in (0, 1):
                resource_spans.append({
                    "resource": {"attributes": [{"key": SERVICE_NAME_KEY, "value": {"stringValue": "emailservice"}}]},
                    "scopeSpans": [{"spans": [{
                        "traceId": trace_id, "spanId": email_span_id, "parentSpanId": co_span_id,
                        "name": "send_order_confirmation", "kind": 2,
                        "startTimeUnixNano": str(start_t + 190_000_000), "endTimeUnixNano": str(start_t + 240_000_000),
                        "status": {"code": 1}
                    }]}]
                })

        # Add 3 runs of view_product
        for i in range(3):
            trace_id = uuid.uuid4().hex
            root_span_id = uuid.uuid4().hex[:16]
            prod_span_id = uuid.uuid4().hex[:16]
            curr_span_id = uuid.uuid4().hex[:16]
            rec_span_id = uuid.uuid4().hex[:16]

            start_t = now_nano - ((i + 5) * 60 * 1_000_000_000)

            # Frontend root
            resource_spans.extend([{
                "resource": {"attributes": [{"key": SERVICE_NAME_KEY, "value": {"stringValue": "frontend"}}]},
                "scopeSpans": [{"spans": [{
                    "traceId": trace_id, "spanId": root_span_id,
                    "name": "GET /product/66VCHSJNUP", "kind": 1,
                    "startTimeUnixNano": str(start_t), "endTimeUnixNano": str(start_t + 50_000_000),
                    "status": {"code": 1}
                }]}]
            },
            # ProductCatalogService (Mandatory)
            {
                "resource": {"attributes": [{"key": SERVICE_NAME_KEY, "value": {"stringValue": "productcatalogservice"}}]},
                "scopeSpans": [{"spans": [{
                    "traceId": trace_id, "spanId": prod_span_id, "parentSpanId": root_span_id,
                    "name": "GetProduct", "kind": 2,
                    "startTimeUnixNano": str(start_t + 5_000_000), "endTimeUnixNano": str(start_t + 20_000_000),
                    "status": {"code": 1}
                }]}]
            },
            # CurrencyService (Mandatory)
            {
                "resource": {"attributes": [{"key": SERVICE_NAME_KEY, "value": {"stringValue": "currencyservice"}}]},
                "scopeSpans": [{"spans": [{
                    "traceId": trace_id, "spanId": curr_span_id, "parentSpanId": root_span_id,
                    "name": "ConvertCurrency", "kind": 2,
                    "startTimeUnixNano": str(start_t + 22_000_000), "endTimeUnixNano": str(start_t + 35_000_000),
                    "status": {"code": 1}
                }]}]
            }])
            # RecommendationService (Optional: run 0 and 1)
            if i in (0, 1):
                resource_spans.append({
                    "resource": {"attributes": [{"key": SERVICE_NAME_KEY, "value": {"stringValue": "recommendationservice"}}]},
                    "scopeSpans": [{"spans": [{
                        "traceId": trace_id, "spanId": rec_span_id, "parentSpanId": root_span_id,
                        "name": "ListRecommendations", "kind": 2,
                        "startTimeUnixNano": str(start_t + 36_000_000), "endTimeUnixNano": str(start_t + 48_000_000),
                        "status": {"code": 1}
                    }]}]
                })

        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(endpoint, json={"resourceSpans": resource_spans})
                return res.status_code == 200
        except Exception as err:
            logger.warning("Failed to seed demo traces to Jaeger OTLP at %s: %s", endpoint, err)
            return False


