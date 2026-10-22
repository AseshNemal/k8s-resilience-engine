"""Tracing infrastructure: what a Jaeger query service reports about the telemetry it has received.

This package is shared and independent. It reads Jaeger through the existing ``JaegerClient`` and imports
nothing from ``app.engine`` or ``app.shared``: it never analyses traces, classifies a dependency or builds a
workflow. It also never sends, seeds or invents telemetry. Everything it reports is something Jaeger said, and
what Jaeger could not say is reported as unknown. The interface is described in docs/TRACING_INFRASTRUCTURE.md.
"""

from app.integrations.tracing.applications import ServiceState, match_applications
from app.integrations.tracing.endpoint import TracingConnection, resolve_connection
from app.integrations.tracing.status import TelemetryState, read_status

__all__ = [
    "ServiceState",
    "TelemetryState",
    "TracingConnection",
    "match_applications",
    "read_status",
    "resolve_connection",
]
