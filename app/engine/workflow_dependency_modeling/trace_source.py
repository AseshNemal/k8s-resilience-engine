"""Trace-query port and the current Jaeger-backed adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.integrations.jaeger.client import JaegerClient


class TraceSource(Protocol):
    """Evidence backend boundary consumed by Component 1 orchestration."""

    def fetch_workflow_traces(
        self,
        *,
        service_name: str,
        operation: str,
        lookback: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class JaegerTraceSource:
    """Read workflow traces from the Jaeger Query API."""

    client: JaegerClient

    @classmethod
    def from_url(cls, base_url: str) -> "JaegerTraceSource":
        return cls(client=JaegerClient(base_url=base_url))

    def fetch_workflow_traces(
        self,
        *,
        service_name: str,
        operation: str,
        lookback: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self.client.fetch_traces(
            service_name=service_name,
            operation=operation,
            lookback=lookback,
            limit=limit,
        )
