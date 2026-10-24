"""The HTTP side of a workflow probe: one GET to a loopback address, with the answer reduced to what the probe judges.

A validation probe reaches the application under test through a port the test cluster publishes on the developer's own
machine (for kind, a NodePort mapped by the cluster config on the control-plane container). So the fetch only ever talks to
``127.0.0.1``, ``localhost`` or ``::1``; any other host is refused before a connection is made. That keeps a probe from being
pointed at an arbitrary address, and a probe has no way to send a body, follow a redirect or use a proxy.

The result has the attributes the engine's probe reads (status, body, latency_ms, error); this module imports nothing from
the engine. An unreachable port, a timeout or a reset is not an exception: it is ``error`` set to the exception's type name, which
the probe records as a failed sample.
"""

import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class Fetched:
    status: int | None
    body: str
    latency_ms: float | None
    error: str | None


# Ports an application is normally published on from a local cluster: the web defaults, the common development ports, the port the
# validation cluster maps its NodePort to, and the NodePort range. Anything else on this machine (a database, ssh, the Docker API) is not
# reachable through a probe. More can be allowed with RESILIENCE_PROBE_PORTS (comma-separated), set on the backend, never by a request.
DEFAULT_PORTS = frozenset({80, 443, 3000, 8000, 8080, 18080})
NODE_PORTS = range(30000, 32768)


def allowed_probe_port(port: int | None, scheme: str = "http", extra: str | None = None) -> bool:
    chosen = port or (443 if scheme == "https" else 80)
    configured = {int(p) for p in (extra if extra is not None else os.environ.get("RESILIENCE_PROBE_PORTS", "")).split(",") if p.strip().isdigit()}
    return chosen in DEFAULT_PORTS or chosen in NODE_PORTS or chosen in configured


def require_loopback(url: str) -> str:
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        raise ValueError(f"a probe only reaches this machine's loopback address, not {url!r}")
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f"{url!r} has an invalid port") from None
    if not allowed_probe_port(port, parts.scheme):
        raise ValueError(f"a probe only reaches the ports an application is published on, not {port}")
    return url


def through_host_gateway(url: str, gateway: str | None) -> str:
    """The URL actually requested. Inside the extension's backend container ``127.0.0.1`` is the container itself, not the developer's
    machine, so a loopback address the developer gave is reached through the container's route to the host (``host.docker.internal``).
    The host is replaced only for a loopback URL that already passed ``require_loopback``; the port, path and query are kept."""
    if not gateway:
        return url
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"{gateway}{port}", parts.path, parts.query, parts.fragment))


class LoopbackHttpFetch:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, max_body_bytes: int = MAX_BODY_BYTES,
                 host_gateway: str | None = None) -> None:
        self._transport, self._max = transport, max_body_bytes
        self._gateway = host_gateway if host_gateway is not None else (os.environ.get("RESILIENCE_PROBE_HOST_GATEWAY") or None)

    def __call__(self, spec) -> Fetched:
        require_loopback(spec.url)
        target = through_host_gateway(spec.url, self._gateway)
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=spec.timeout_s, trust_env=False, follow_redirects=False,
                              transport=self._transport) as http:
                response = http.request(spec.method, target)
        except (httpx.HTTPError, OSError) as error:
            return Fetched(None, "", None, type(error).__name__)
        elapsed = (time.perf_counter() - started) * 1000.0
        return Fetched(response.status_code, response.text[: self._max], elapsed, None)
