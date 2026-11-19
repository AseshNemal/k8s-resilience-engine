"""The real readers for a timeline: one cluster gateway and (optionally) one HTTP probe, turned into ``Readers``.

The gateway is duck-typed (``read_graph``, ``read_node_states``, ``read_node_usage``, ``read_warning_events``,
``read_live_activity``, ``read_pod_log_tail``); the engine imports no integration. Every reader is read only and bounded, and none
of them swallows its own failure: the sampler counts a failing reader as a gap, which is the honest record of a cluster that
could not answer. The one exception is per-pod log reads, where a pod on a stopped node is *expected* not to answer.
"""

from collections.abc import Callable
from typing import Any

from app.engine.recommendation_evaluation_validation.monitoring.sampler import Readers

CRASHING = frozenset({"CrashLoopBackOff", "Error", "RunContainerError", "CreateContainerError", "ImagePullBackOff", "ErrImagePull",
                      "OOMKilled", "CreateContainerConfigError"})
MAX_LOG_PODS = 3


def _why(reason: str | None, status: int | None) -> str | None:
    """A failed request's reason without the page's own words: the status or the kind of mismatch, never the text that matched."""
    if not reason:
        return None
    if reason.startswith("page reports a failure"):
        return "the page reports a failure"
    if reason.startswith("required text missing"):
        return "the page lacks the required text"
    if reason.startswith("request failed"):
        return reason.split(":", 1)[-1].strip()[:60] or "request failed"
    return f"HTTP {status}" if status is not None else reason[:60]


def request_reader(probe: Any) -> Callable[[], dict[str, Any]]:
    """One real request to the application per call, judged by the same rule as the workflow check."""
    def read() -> dict[str, Any]:
        sample = probe.sample()
        return {"ok": bool(sample.ok), "ms": None if sample.latency_ms is None else int(round(sample.latency_ms)),
                "status": sample.status, "why": None if sample.ok else _why(sample.reason, sample.status)}
    return read


def gateway_readers(gateway: Any, namespace: str, *, probe: Any | None = None, since_iso: str | None = None,
                    with_events: bool = True, with_logs: bool = True) -> Readers:
    def services() -> dict[str, int | None]:
        graph = gateway.read_graph(namespace)
        return {service.name: sum(1 for pod in service.pods if pod.ready) for service in graph.services}

    def nodes() -> dict[str, bool | None]:
        return {name: state.get("ready") for name, state in gateway.read_node_states().items()}

    def usage() -> dict[str, Any]:
        return gateway.read_node_usage()

    def events(_since_s: float) -> list[dict[str, Any]]:
        out = []
        for event in gateway.read_warning_events(namespace):
            if since_iso and (event.get("at") or "") < since_iso:
                continue
            out.append({k: event.get(k) for k in ("key", "reason", "object", "message", "count")})
        return out[-20:]

    def logs() -> list[tuple[str, list[str]]]:
        names = sorted({d for s in gateway.read_graph(namespace).services for d in s.deployment_names})
        found: list[tuple[str, list[str]]] = []
        for deployment in gateway.read_live_activity(namespace, names):
            for pod in deployment.get("pods") or []:
                if len(found) >= MAX_LOG_PODS:
                    return found
                if pod.get("ready") is not True and pod.get("state") in CRASHING:
                    try:
                        lines = gateway.read_pod_log_tail(namespace, pod["name"])
                    except Exception:      # noqa: BLE001  (a pod that cannot answer has no log to show; that is not an error of the run)
                        continue
                    if lines:
                        found.append((pod["name"], lines))
        return found

    return Readers(services=services, nodes=nodes, request=None if probe is None else request_reader(probe), usage=usage,
                   events=events if with_events else None, logs=logs if with_logs else None)
