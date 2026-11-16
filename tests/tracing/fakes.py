"""A stand-in for the trace service: a query API that only answers, never receives."""

from __future__ import annotations

NOW = 1_800_000_000.0  # a fixed "now", in epoch seconds


def trace_started(seconds_before_now: float) -> dict:
    """A Jaeger trace whose earliest span began that long before NOW (Jaeger reports microseconds)."""
    began = int((NOW - seconds_before_now) * 1_000_000)
    return {"traceID": "a" * 32, "spans": [{"startTime": began + 5_000}, {"startTime": began}]}


class FakeSource:
    base_url = "http://jaeger.test:16686"

    def __init__(self, services=None, traces=None, healthy=True, services_fail=False, fail_for=()):
        self.healthy = healthy
        self.services = list(services or [])
        self.traces = dict(traces or {})  # service name -> list of traces
        self.services_fail = services_fail
        self.fail_for = set(fail_for)
        self.calls: list[str] = []

    def is_healthy(self) -> bool:
        self.calls.append("is_healthy")
        return self.healthy

    def get_services(self) -> list[str]:
        self.calls.append("get_services")
        if self.services_fail:
            raise ConnectionError("down")
        return list(self.services)

    def fetch_traces(self, service_name, operation=None, limit=100, lookback="1h", tags=None):
        self.calls.append("fetch_traces")
        if service_name in self.fail_for:
            raise ConnectionError("down")
        return list(self.traces.get(service_name, []))[:limit]
