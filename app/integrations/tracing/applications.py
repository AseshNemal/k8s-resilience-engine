"""Is this Kubernetes application producing traces?

The caller names the Kubernetes services of one namespace (the extension already has them from discovery) and
this says, for each, whether Jaeger holds traces from a service of that name. The rule is deliberately narrow:

  * a Jaeger service matches a Kubernetes service when its name is the same (ignoring case) or is the name
    followed by ``.<namespace>``, the form Istio's sidecars report;
  * nothing else matches. A service with a different name is "not seen", not "probably the same".

"Not seen" means Jaeger has no such service. It does not say the application is not instrumented, only that
no trace under that name has arrived, and the wording the page shows says so.
"""

from __future__ import annotations

import re
import time
from enum import Enum
from typing import Any

from app.integrations.tracing.status import DEFAULT_FRESH_SECONDS, TraceSource, _iso, excluded_services, last_trace_at

MAX_SERVICES = 50
_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")


class ServiceState(str, Enum):
    PRODUCING = "PRODUCING"  # a trace began within the freshness window
    STALE = "STALE"  # Jaeger knows the service, but its newest trace is older than the window or outside the lookback
    NOT_SEEN = "NOT_SEEN"  # Jaeger has no service of this name


def valid_name(value: str) -> bool:
    return bool(_NAME.fullmatch(value))


def jaeger_names_for(service: str, namespace: str, jaeger_services: list[str]) -> list[str]:
    wanted = {service.lower(), f"{service}.{namespace}".lower()}
    return sorted(name for name in jaeger_services if name.lower() in wanted)


def match_applications(
    source: TraceSource,
    namespace: str,
    services: list[str],
    *,
    now: float | None = None,
    fresh_seconds: int = DEFAULT_FRESH_SECONDS,
) -> dict[str, Any]:
    """Per-service trace state for ``services`` of ``namespace``. Raises ConnectionError if Jaeger cannot answer."""
    if not valid_name(namespace) or len(services) > MAX_SERVICES or not all(valid_name(s) for s in services):
        raise ValueError("namespace and service names must be Kubernetes names, at most %d services" % MAX_SERVICES)
    observed = time.time() if now is None else now
    skip = excluded_services()
    known = [name for name in source.get_services() if name.lower() not in skip]

    rows = []
    for service in sorted(set(services)):
        matches = jaeger_names_for(service, namespace, known)
        newest: float | None = None
        for name in matches:
            began = last_trace_at(source, name)
            if began is not None:
                newest = began if newest is None else max(newest, began)
        if not matches:
            state = ServiceState.NOT_SEEN
        elif newest is not None and observed - newest <= fresh_seconds:
            state = ServiceState.PRODUCING
        else:
            state = ServiceState.STALE
        rows.append(
            {
                "service": service,
                "state": state.value,
                "jaegerServices": matches,
                "lastTraceAt": _iso(newest) if newest is not None else None,
            }
        )
    producing = sum(1 for row in rows if row["state"] == ServiceState.PRODUCING.value)
    return {
        "namespace": namespace,
        "services": rows,
        "producing": producing,
        "total": len(rows),
        "freshSeconds": fresh_seconds,
        "observedAt": _iso(observed),
    }
