"""What the recorded root requests of one operation say about how it is called and what it answered.

The setup of a workflow experiment needs a success condition and an address to send the workflow to. Neither is
assumed for any application: both are read from the root spans of the traces that were inspected, reported with
the counts they come from, and offered to a person as suggestions to confirm or edit. Anything the traces do not
record (request bodies, business meaning, importance) is reported as unknown, never filled in.
"""

from __future__ import annotations

import os
from collections import Counter
from statistics import median
from typing import Any
from urllib.parse import urlsplit

from app.engine.workflow_dependency_modeling.auto_discovery import find_root_spans
from app.engine.workflow_dependency_modeling.trace_reader import Span, parse_spans

_STATUS_TAGS = ("http.response.status_code", "http.status_code")
_SCHEME_TAGS = ("url.scheme", "http.scheme")
_HOST_TAGS = ("server.address", "net.host.name", "http.host")
_PORT_TAGS = ("server.port", "net.host.port")
_SPAN_STATUS_TAGS = ("otel.status_code", "status.code")
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
GATEWAY_SETTING = "RESILIENCE_PROBE_HOST_GATEWAY"

SUCCESS_RULE = ("A response code is suggested as success when it is below 400 and none of the recorded requests that "
                "returned it has an error span status.")


def _tag(span: Span, names: tuple[str, ...]) -> str | None:
    return next((str(span.tags[name]) for name in names if span.tags.get(name) not in (None, "")), None)


def _span_error(span: Span) -> bool:
    status = (_tag(span, _SPAN_STATUS_TAGS) or "").upper()
    return bool(span.error) or status in {"ERROR", "2"}


def _address(span: Span) -> str | None:
    scheme, host, port = _tag(span, _SCHEME_TAGS), _tag(span, _HOST_TAGS), _tag(span, _PORT_TAGS)
    if not scheme or not host:
        return None
    if ":" in host and not host.startswith("["):
        host = host.split(":", 1)[0] if host.count(":") == 1 else f"[{host}]"
    return f"{scheme}://{host}" + (f":{port}" if port and port.isdigit() else "")


def _reachable(url: str) -> tuple[str, str | None]:
    """The address this backend can use for ``url``, and why it differs when it does."""
    gateway = (os.environ.get(GATEWAY_SETTING) or "").strip()
    parts = urlsplit(url)
    if gateway and (parts.hostname or "") in _LOOPBACK:
        port = f":{parts.port}" if parts.port else ""
        return (f"{parts.scheme}://{gateway}{port}",
                f"The application recorded the loopback address {parts.hostname}; this backend runs in a container, "
                f"so it reaches the same port through {gateway} ({GATEWAY_SETTING}).")
    return url, None


def summarize_root_requests(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Count status codes, span errors, durations and addresses over the root spans of ``traces``."""
    roots: list[Span] = []
    for trace in traces:
        found = find_root_spans({str(trace.get("traceID", "")): parse_spans([trace])})
        roots.extend(found.values())
    codes: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    addresses: Counter[str] = Counter()
    for root in roots:
        code = _tag(root, _STATUS_TAGS) or "unrecorded"
        codes[code] += 1
        if _span_error(root):
            errors[code] += 1
        address = _address(root)
        if address:
            addresses[address] += 1
    status_codes = [
        {"code": code, "count": count, "withSpanError": errors[code]}
        for code, count in sorted(codes.items(), key=lambda item: (-item[1], item[0]))
    ]
    # Suggested success code = observed code < 400 whose recorded requests never carried an error span status
    suggested_codes = [
        int(item["code"]) for item in status_codes
        if item["code"].isdigit() and int(item["code"]) < 400 and item["withSpanError"] == 0
    ]
    durations = sorted(root.duration_us / 1000 for root in roots)
    observed_addresses = [{"url": url, "count": count} for url, count in addresses.most_common()]
    address_suggestion = None
    if observed_addresses:
        recorded = observed_addresses[0]["url"]
        reachable, note = _reachable(recorded)
        address_suggestion = {"recordedUrl": recorded, "url": reachable, "note": note,
                              "basis": f"{observed_addresses[0]['count']} of {len(roots)} recorded requests were received at this address"}
    return {
        "requestCount": len(roots),
        "statusCodes": status_codes,
        "durationMs": ({"min": round(durations[0], 3), "median": round(median(durations), 3), "max": round(durations[-1], 3)}
                       if durations else None),
        "observedAddresses": observed_addresses,
        "suggestedSuccessStatusCodes": suggested_codes,
        "successRule": SUCCESS_RULE,
        "suggestedApplicationUrl": address_suggestion,
        "notRecorded": ["request body and form fields", "the business outcome the request should achieve",
                        "business importance"],
    }
