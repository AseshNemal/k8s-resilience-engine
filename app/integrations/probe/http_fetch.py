"""The HTTP side of a workflow probe: one GET to a loopback address, with the answer reduced to what the probe judges.

A validation probe reaches the application under test through a port the test cluster publishes on the developer's own
machine (for kind, a NodePort mapped by the cluster config on the control-plane container). So the fetch only ever talks to
``127.0.0.1``, ``localhost`` or ``::1``; any other host is refused before a connection is made. That keeps a probe from being
pointed at an arbitrary address, and a probe has no way to send a body, follow a redirect or use a proxy.

The result has the attributes the engine's probe reads (status, body, latency_ms, error); this module imports nothing from
the engine. An unreachable port, a timeout or a reset is not an exception: it is ``error`` set to the exception's type name, which
the probe records as a failed sample.
"""

import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class Fetched:
    status: int | None
    body: str
    latency_ms: float | None
    error: str | None


def require_loopback(url: str) -> str:
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        raise ValueError(f"a probe only reaches this machine's loopback address, not {url!r}")
    return url


class LoopbackHttpFetch:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, max_body_bytes: int = MAX_BODY_BYTES) -> None:
        self._transport, self._max = transport, max_body_bytes

    def __call__(self, spec) -> Fetched:
        require_loopback(spec.url)
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=spec.timeout_s, trust_env=False, follow_redirects=False,
                              transport=self._transport) as http:
                response = http.request(spec.method, spec.url)
        except (httpx.HTTPError, OSError) as error:
            return Fetched(None, "", None, type(error).__name__)
        elapsed = (time.perf_counter() - started) * 1000.0
        return Fetched(response.status_code, response.text[: self._max], elapsed, None)
