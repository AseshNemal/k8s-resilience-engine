"""Trace-query port and the current Jaeger-backed adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import re
import time
from typing import Any, Mapping, Protocol

import httpx

from app.engine.workflow_dependency_modeling.models import ComponentDataError
from app.engine.workflow_dependency_modeling.telemetry_normalizer import (
    normalize_otlp_traces,
    normalize_zipkin_traces,
)
from app.integrations.jaeger.client import JaegerClient


_SECRET_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class TraceSourceKind(str, Enum):
    JAEGER = "jaeger"
    ZIPKIN = "zipkin"
    TEMPO = "tempo"


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
            raise ComponentDataError(
                "traceSource type must be jaeger, zipkin, or tempo"
            ) from err
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


def _configuration_headers(
    configuration: TraceSourceConfiguration,
    secrets: Mapping[str, str] | None,
) -> dict[str, str]:
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
    return headers


@dataclass(frozen=True)
class OfflineJaegerTraceSource:
    """Read an immutable in-memory Jaeger export through the generic port."""

    traces: tuple[dict[str, Any], ...]

    @classmethod
    def from_payload(cls, payload: Any) -> "OfflineJaegerTraceSource":
        if isinstance(payload, Mapping):
            payload = payload.get("data")
        if not isinstance(payload, list) or not payload:
            raise ComponentDataError(
                "OfflineJaegerTraceSource requires a non-empty Jaeger trace export"
            )
        if any(not isinstance(item, Mapping) for item in payload):
            raise ComponentDataError("Offline Jaeger traces must be objects")
        return cls(tuple(dict(item) for item in payload))

    def fetch_workflow_traces(
        self,
        *,
        service_name: str,
        operation: str,
        lookback: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Offline exports are immutable snapshots, so lookback is provenance
        # supplied by the exporter rather than a query applied here.
        del lookback
        from app.engine.workflow_dependency_modeling.auto_discovery import (
            select_workflow_traces,
        )

        matching_service = [
            trace
            for trace in self.traces
            if not service_name
            or any(
                process.get("serviceName") == service_name
                for process in trace.get("processes", {}).values()
            )
        ]
        selected = (
            select_workflow_traces(matching_service, operation)
            if operation
            else matching_service
        )
        return list(selected[:limit])

    def fetch_trace(self, trace_id: str) -> list[dict[str, Any]]:
        return [trace for trace in self.traces if trace.get("traceID") == trace_id]


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
        if configuration.kind is not TraceSourceKind.JAEGER:
            raise ValueError("JaegerTraceSource requires a jaeger configuration")
        headers = _configuration_headers(configuration, secrets)
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

    def get_services(self) -> list[str]:
        return self.client.get_services()

    def fetch_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """Return only evidence addressed by the registered trace ID."""
        return self.client.fetch_trace(trace_id)


def _lookback_milliseconds(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)([smhdw])", value.strip().lower())
    if match is None:
        raise ComponentDataError("lookback must use s, m, h, d, or w (for example 1h)")
    amount, unit = int(match.group(1)), match.group(2)
    milliseconds = {
        "s": 1_000,
        "m": 60_000,
        "h": 3_600_000,
        "d": 86_400_000,
        "w": 604_800_000,
    }
    return amount * milliseconds[unit]


def _http_json(
    base_url: str,
    path: str,
    *,
    params: Mapping[str, Any] | None,
    headers: Mapping[str, str],
    timeout: float,
    transport: httpx.BaseTransport | None,
) -> Any:
    try:
        with httpx.Client(
            timeout=timeout, headers=headers, transport=transport
        ) as client:
            response = client.get(f"{base_url.rstrip('/')}{path}", params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
    except ComponentDataError:
        raise
    except Exception as err:
        raise ConnectionError(
            f"Could not query telemetry backend at {base_url}: {err}"
        ) from err


@dataclass(frozen=True)
class ZipkinTraceSource:
    """Read and normalize Zipkin v2 Query API traces."""

    base_url: str
    headers: Mapping[str, str] | None = None
    timeout_seconds: float = 5.0
    transport: httpx.BaseTransport | None = None

    def get_services(self) -> list[str]:
        payload = _http_json(
            self.base_url,
            "/api/v2/services",
            params=None,
            headers=dict(self.headers or {}),
            timeout=self.timeout_seconds,
            transport=self.transport,
        )
        return sorted(str(item) for item in (payload or []) if str(item).strip())

    def fetch_workflow_traces(
        self,
        *,
        service_name: str,
        operation: str,
        lookback: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "serviceName": service_name,
            "limit": limit,
            "endTs": int(time.time() * 1_000),
            "lookback": _lookback_milliseconds(lookback),
        }
        if operation:
            params["spanName"] = operation
        payload = _http_json(
            self.base_url,
            "/api/v2/traces",
            params=params,
            headers=dict(self.headers or {}),
            timeout=self.timeout_seconds,
            transport=self.transport,
        )
        return normalize_zipkin_traces(payload or [])

    def fetch_trace(self, trace_id: str) -> list[dict[str, Any]]:
        if not re.fullmatch(r"[0-9a-fA-F]{16,32}", trace_id):
            raise ValueError("trace_id must be a 16- or 32-character hexadecimal ID")
        payload = _http_json(
            self.base_url,
            f"/api/v2/trace/{trace_id.lower()}",
            params=None,
            headers=dict(self.headers or {}),
            timeout=self.timeout_seconds,
            transport=self.transport,
        )
        return normalize_zipkin_traces(payload or [])


def _traceql_literal(value: str) -> str:
    # TraceQL backtick strings avoid quote ambiguity. Reject backticks rather
    # than attempting to create a backend-specific escaping dialect.
    if "`" in value:
        raise ComponentDataError("Tempo query values must not contain backticks")
    return f"`{value}`"


@dataclass(frozen=True)
class TempoTraceSource:
    """Read Tempo search results and normalize its OTLP JSON trace response."""

    base_url: str
    headers: Mapping[str, str] | None = None
    timeout_seconds: float = 10.0
    transport: httpx.BaseTransport | None = None

    def get_services(self) -> list[str]:
        payload = _http_json(
            self.base_url,
            "/api/v2/search/tag/resource.service.name/values",
            params=None,
            headers=dict(self.headers or {}),
            timeout=self.timeout_seconds,
            transport=self.transport,
        )
        if isinstance(payload, Mapping):
            values = payload.get("tagValues", payload.get("values", []))
        else:
            values = []
        services = [
            item.get("value") if isinstance(item, Mapping) else item
            for item in values
        ]
        return sorted(str(item) for item in services if str(item or "").strip())

    def fetch_workflow_traces(
        self,
        *,
        service_name: str,
        operation: str,
        lookback: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses = [f"resource.service.name = {_traceql_literal(service_name)}"]
        if operation:
            clauses.append(f"name = {_traceql_literal(operation)}")
        end_seconds = int(time.time())
        payload = _http_json(
            self.base_url,
            "/api/search",
            params={
                "q": "{ " + " && ".join(clauses) + " }",
                "limit": limit,
                "start": end_seconds - (_lookback_milliseconds(lookback) // 1_000),
                "end": end_seconds,
            },
            headers=dict(self.headers or {}),
            timeout=self.timeout_seconds,
            transport=self.transport,
        )
        summaries = payload.get("traces", []) if isinstance(payload, Mapping) else []
        traces: list[dict[str, Any]] = []
        for summary in summaries[:limit]:
            trace_id = summary.get("traceID") if isinstance(summary, Mapping) else None
            if trace_id:
                traces.extend(self.fetch_trace(str(trace_id)))
        return traces

    def fetch_trace(self, trace_id: str) -> list[dict[str, Any]]:
        if not re.fullmatch(r"[0-9a-fA-F]{16,32}", trace_id):
            raise ValueError("trace_id must be a 16- or 32-character hexadecimal ID")
        payload = _http_json(
            self.base_url,
            f"/api/v2/traces/{trace_id.lower()}",
            params=None,
            headers=dict(self.headers or {}),
            timeout=self.timeout_seconds,
            transport=self.transport,
        )
        return [] if payload is None else normalize_otlp_traces(payload)


def create_trace_source(
    configuration: TraceSourceConfiguration,
    *,
    secrets: Mapping[str, str] | None = None,
) -> TraceSource:
    """Construct the configured live adapter without persisting credentials."""
    headers = _configuration_headers(configuration, secrets)
    if configuration.kind is TraceSourceKind.JAEGER:
        return JaegerTraceSource(
            JaegerClient(base_url=configuration.endpoint, headers=headers)
        )
    if configuration.kind is TraceSourceKind.ZIPKIN:
        return ZipkinTraceSource(configuration.endpoint, headers=headers)
    if configuration.kind is TraceSourceKind.TEMPO:
        return TempoTraceSource(configuration.endpoint, headers=headers)
    raise ValueError(f"Unsupported trace source: {configuration.kind}")
