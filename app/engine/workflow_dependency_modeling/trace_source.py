"""Trace-query port and the current Jaeger-backed adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.integrations.jaeger.client import JaegerClient


class TraceSourceKind(str, Enum):
    JAEGER = "jaeger"


class TraceAuthenticationKind(str, Enum):
    NONE = "none"


@dataclass(frozen=True)
class TraceSourceConfiguration:
    """Persistable connection contract without embedded credentials."""

    kind: TraceSourceKind
    endpoint: str
    authentication: TraceAuthenticationKind = TraceAuthenticationKind.NONE
    secret_env: str | None = None

    @classmethod
    def from_mapping(cls, data: Any) -> "TraceSourceConfiguration":
        if not isinstance(data, dict):
            raise ComponentDataError("traceSource must be an object")
        try:
            kind = TraceSourceKind(data.get("type"))
        except ValueError as err:
            raise ComponentDataError("traceSource type must be jaeger") from err
        endpoint = data.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ComponentDataError("traceSource endpoint is required")
        authentication_data = data.get("authentication", {"type": "none"})
        if not isinstance(authentication_data, dict):
            raise ComponentDataError("traceSource authentication must be an object")
        try:
            authentication = TraceAuthenticationKind(
                authentication_data.get("type", "none")
            )
        except ValueError as err:
            raise ComponentDataError(
                "traceSource authentication type must currently be none"
            ) from err
        secret_env = authentication_data.get("secretEnv")
        if secret_env is not None:
            raise ComponentDataError(
                "secretEnv is not accepted until authenticated trace sources are enabled"
            )
        return cls(
            kind=kind,
            endpoint=endpoint.strip(),
            authentication=authentication,
            secret_env=secret_env,
        )

    def to_mapping(self) -> dict[str, Any]:
        authentication: dict[str, str] = {"type": self.authentication.value}
        return {
            "type": self.kind.value,
            "endpoint": self.endpoint,
            "authentication": authentication,
        }


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
