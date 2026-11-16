"""Where the trace service is, as the backend sees it.

The query address is the backend's own setting (``WORKFLOW_JAEGER_URL``, the one the Workflows page already
uses), so a Jaeger started by the extension and one the person already runs are treated the same. Nothing in a
request can change it: an address typed by a caller is not accepted here, so this adds no new way to make the
backend contact an arbitrary host.

The OTLP receivers are a different matter. For the extension's own container they sit on the same machine as
the query service on the standard ports, which is a convention of that container, not something Jaeger
reports; for any other Jaeger they are unknown unless the backend is told (``TRACING_OTLP_HTTP_ENDPOINT`` and
``TRACING_OTLP_GRPC_ENDPOINT``). Unknown stays ``None``: it is never guessed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

from app.integrations.jaeger.client import DEFAULT_JAEGER_URL

MANAGED_QUERY_PORT = 16686
MANAGED_OTLP_HTTP_PORT = 4318
MANAGED_OTLP_GRPC_PORT = 4317


@dataclass(frozen=True)
class TracingConnection:
    query_url: str
    otlp_http: str | None
    otlp_grpc: str | None
    otlp_source: str  # "configured", "same-host-standard-ports" or "unknown"

    def to_mapping(self) -> dict:
        return {
            "query": {"url": self.query_url},
            "otlp": {"http": self.otlp_http, "grpc": self.otlp_grpc, "source": self.otlp_source},
        }


def _clean(value: str | None) -> str | None:
    text = (value or "").strip().rstrip("/")
    return text or None


def resolve_connection(environ: Mapping[str, str] | None = None) -> TracingConnection:
    """The query address and, where it can be said, the OTLP receivers."""
    env = os.environ if environ is None else environ
    query_url = _clean(env.get("WORKFLOW_JAEGER_URL")) or DEFAULT_JAEGER_URL
    http_endpoint = _clean(env.get("TRACING_OTLP_HTTP_ENDPOINT"))
    grpc_endpoint = _clean(env.get("TRACING_OTLP_GRPC_ENDPOINT"))
    if http_endpoint or grpc_endpoint:
        return TracingConnection(query_url, http_endpoint, grpc_endpoint, "configured")

    parts = urlsplit(query_url)
    if parts.hostname and parts.port == MANAGED_QUERY_PORT:
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        return TracingConnection(
            query_url,
            f"http://{host}:{MANAGED_OTLP_HTTP_PORT}",
            f"{host}:{MANAGED_OTLP_GRPC_PORT}",
            "same-host-standard-ports",
        )
    return TracingConnection(query_url, None, None, "unknown")
