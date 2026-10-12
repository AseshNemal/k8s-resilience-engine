"""Trace-query port and the current Jaeger-backed adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import re
from typing import Any, Mapping, Protocol

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.integrations.jaeger.client import JaegerClient


_SECRET_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class TraceSourceKind(str, Enum):
    JAEGER = "jaeger"


class TraceAuthenticationKind(str, Enum):
    NONE = "none"
    BEARER_ENV = "bearer_env"


@dataclass(frozen=True)
class TraceSourceConfiguration:
    """Persistable connection contract without embedded credentials."""

    kind: TraceSourceKind
    endpoint: str
    authentication: TraceAuthenticationKind = TraceAuthenticationKind.NONE
    secret_env: str | None = None

    @classmethod
    def from_mapping(cls, data: Any) -> "TraceSourceConfiguration":
        if not isinstance(data, Mapping):
            raise ComponentDataError("traceSource must be an object")
        try:
            kind = TraceSourceKind(data.get("type"))
        except ValueError as err:
            raise ComponentDataError("traceSource type must be jaeger") from err
        endpoint = data.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ComponentDataError("traceSource endpoint is required")
        authentication_data = data.get("authentication", {"type": "none"})
        if not isinstance(authentication_data, Mapping):
            raise ComponentDataError("traceSource authentication must be an object")
        try:
            authentication = TraceAuthenticationKind(
                authentication_data.get("type", "none")
            )
        except ValueError as err:
            raise ComponentDataError(
                "traceSource authentication type must be none or bearer_env"
            ) from err
        secret_env = authentication_data.get("secretEnv")
        if authentication is TraceAuthenticationKind.BEARER_ENV:
            if not isinstance(secret_env, str) or not _SECRET_ENV.fullmatch(secret_env):
                raise ComponentDataError(
                    "bearer_env authentication requires a valid secretEnv name"
                )
        elif secret_env is not None:
            raise ComponentDataError(
                "secretEnv is only valid for bearer_env authentication"
            )
        return cls(
            kind=kind,
            endpoint=endpoint.strip(),
            authentication=authentication,
            secret_env=secret_env,
        )

    def to_mapping(self) -> dict[str, Any]:
        authentication: dict[str, str] = {"type": self.authentication.value}
        if self.secret_env is not None:
            authentication["secretEnv"] = self.secret_env
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

    def fetch_trace(self, trace_id: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class JaegerTraceSource:
    """Read workflow traces from the Jaeger Query API."""

    client: JaegerClient

    @classmethod
    def from_url(cls, base_url: str) -> "JaegerTraceSource":
        return cls(client=JaegerClient(base_url=base_url))

    @classmethod
    def from_configuration(
        cls,
        configuration: TraceSourceConfiguration,
        *,
        secrets: Mapping[str, str] | None = None,
    ) -> "JaegerTraceSource":
        headers: dict[str, str] = {}
        if configuration.authentication is TraceAuthenticationKind.BEARER_ENV:
            secret_env = configuration.secret_env
            if secret_env is None:  # pragma: no cover - guarded by the model
                raise ValueError("Bearer authentication has no secret reference")
            secret_values = os.environ if secrets is None else secrets
            token = secret_values.get(secret_env)
            if not token:
                raise ValueError(
                    f"Required trace-source secret environment variable "
                    f"'{secret_env}' is not set"
                )
            headers["Authorization"] = f"Bearer {token}"
        return cls(
            client=JaegerClient(
                base_url=configuration.endpoint,
                headers=headers,
            )
        )

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

    def fetch_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """Return only evidence addressed by the registered trace ID."""
        return self.client.fetch_trace(trace_id)
