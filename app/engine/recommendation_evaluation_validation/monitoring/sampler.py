"""The clock that fills a timeline: read the application at an interval while a failure happens, and keep what came back.

A sampler only reads. Its readers are given to it as plain callables (see ``readers.py`` for the real ones), so it can be tested
without a cluster and cannot do anything the readers cannot. It never decides whether a failure is happening: the marks it records
come from whoever drives it (the loop says "stop issued", "node ready") and from what the readers saw (a node that reads NotReady).

Two things keep it honest when the cluster misbehaves, which is the whole point of running it during a failure:

* A reader that raises or times out leaves that value ``None`` for that sample and is counted in ``reads``; one slow or failing
  reader never holds up the others. The request (an HTTP call to the application) runs on its own thread for the same reason: a
  request that hangs until its timeout is exactly what an outage looks like, and it must not stop the readiness samples.
* The thread has a hard stop. ``stop()`` always returns a finished timeline, and a sampler that was never stopped ends itself after
  ``max_seconds``.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.engine.recommendation_evaluation_validation.monitoring.timeline import Timeline


@dataclass
class Readers:
    """What the sampler can read. Every field is optional; a missing reader is simply never sampled.

    * ``services()``: ready replicas per service, ``{name: int}`` (``None`` for one that could not be read).
    * ``nodes()``: readiness per node, ``{name: True|False|None}``.
    * ``request()``: one real request to the application, ``{"ok": bool, "ms": int|None, "status": int|None, "why": str|None}``.
    * ``usage()``: ``{"cpu": {node: millicores}, "mem": {node: MiB}, "source": str}``; a node with no reading is absent or ``None``.
    * ``events(since_s)``: Warning events seen since the recording began, ``[{"key", "reason", "object", "message", "count"}]``.
    * ``logs()``: the tail of pods that are not healthy, ``[(pod, [line, ...])]``.
    """

    services: Callable[[], dict[str, int | None]] | None = None
    nodes: Callable[[], dict[str, bool | None]] | None = None
    request: Callable[[], dict[str, Any]] | None = None
    usage: Callable[[], dict[str, Any]] | None = None
    events: Callable[[float], list[dict[str, Any]]] | None = None
    logs: Callable[[], list[tuple[str, list[str]]]] | None = None


def _code(error: Exception) -> str:
    return str(getattr(error, "code", None) or type(error).__name__)


class TimelineSampler:
    def __init__(self, readers: Readers, *, interval_s: float = 1.0, usage_every: int = 3, slow_every: int = 5,
                 max_seconds: float = 3600.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, on_update: Callable[[dict[str, Any]], None] | None = None,
                 update_every_s: float = 3.0, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._readers, self._interval = readers, max(float(interval_s), 0.05)
        self._usage_every, self._slow_every, self._max = max(usage_every, 1), max(slow_every, 1), max_seconds
        self._clock, self._sleep, self._on_update, self._update_every = clock, sleep, on_update, update_every_s
        self._lock = threading.RLock()
        self._origin = self._clock()
        self._timeline = Timeline(started_at=now().isoformat(), interval_s=self._interval)
        self._ticks = 0
        self._thread: threading.Thread | None = None
        self._request_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_update = float("-inf")
        self._last_nodes: dict[str, bool | None] = {}
        self._cpu: dict[str, Any] = {}
        self._mem: dict[str, Any] = {}

    # -- the record --------------------------------------------------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return self._clock() - self._origin

    def mark(self, kind: str, detail: str = "") -> None:
        with self._lock:
            self._timeline.add_mark(self.elapsed, kind, detail)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._timeline.to_dict()

    @property
    def samples_taken(self) -> int:
        with self._lock:
            return len(self._timeline.samples)

    # -- one reading -------------------------------------------------------------------------------------------------

    def _read(self, name: str, reader: Callable[..., Any] | None, *args: Any) -> Any:
        if reader is None:
            return None
        try:
            value = reader(*args)
        except Exception as error:      # noqa: BLE001  (a failing reader is a gap in the record, never an exception in the run)
            with self._lock:
                self._timeline.count_read(name, False, _code(error))
            return None
        with self._lock:
            self._timeline.count_read(name, True)
        return value

    def step(self, *, inline_request: bool = True) -> dict[str, Any]:
        """One sample: read the application now and add it to the record. Returns the sample."""
        t = round(self.elapsed, 2)
        self._ticks += 1
        ready = self._read("services", self._readers.services)
        nodes = self._read("nodes", self._readers.nodes)
        sample: dict[str, Any] = {"t": t, "ready": ready, "nodes": nodes}
        if nodes is not None:
            for node, state in nodes.items():
                # What the sampler SAW. The caller marks what it DID ("stop", "restore") and when it saw the node return ("node-ready").
                if state is False and self._last_nodes.get(node) is not False:
                    self.mark("node-not-ready", node)
            self._last_nodes = dict(nodes)
        if self._readers.usage is not None and (self._ticks - 1) % self._usage_every == 0:
            usage = self._read("usage", self._readers.usage)
            if usage:
                self._cpu, self._mem = dict(usage.get("cpu") or {}), dict(usage.get("mem") or {})
                if usage.get("source"):
                    with self._lock:
                        self._timeline.sources["usage"] = usage["source"]
            else:
                self._cpu, self._mem = {}, {}
        if self._cpu or self._mem:
            sample["cpu"], sample["mem"] = dict(self._cpu), dict(self._mem)
        with self._lock:
            if ready and not self._timeline.baseline and not any(m["kind"] == "stop" for m in self._timeline.marks):
                self._timeline.baseline = {k: v for k, v in ready.items() if v is not None}
            self._timeline.add_sample(sample)
        if self._readers.request is not None:
            if inline_request:
                sample["req"] = self._read("request", self._readers.request)
            else:
                self._start_request(sample)
        if (self._ticks - 1) % self._slow_every == 0:
            self._read_slow()
        return sample

    def _start_request(self, sample: dict[str, Any]) -> None:
        busy = self._request_thread is not None and self._request_thread.is_alive()
        if busy:
            return      # the previous request is still waiting for an answer: that is what an outage looks like; do not pile on

        def run() -> None:
            sample["req"] = self._read("request", self._readers.request)
        self._request_thread = threading.Thread(target=run, daemon=True, name="timeline-request")
        self._request_thread.start()

    def _read_slow(self) -> None:
        events = self._read("events", self._readers.events, self.elapsed) if self._readers.events else None
        for event in events or []:
            with self._lock:
                self._timeline.add_event({**event, "t": round(self.elapsed, 2)})
        logs = self._read("logs", self._readers.logs) if self._readers.logs else None
        for pod, lines in logs or []:
            with self._lock:
                if not any(entry["pod"] == pod for entry in self._timeline.logs):
                    self._timeline.add_log(self.elapsed, pod, lines)

    # -- the thread --------------------------------------------------------------------------------------------------

    def start(self, *, wait_samples: int = 0, wait_timeout_s: float = 30.0) -> "TimelineSampler":
        """Begin sampling on a thread. With ``wait_samples``, return only once that many were taken (the pre-roll), or the timeout."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="timeline-sampler")
        self._thread.start()
        deadline = self._clock() + wait_timeout_s
        while wait_samples and self.samples_taken < wait_samples and self._clock() < deadline and self._thread.is_alive():
            self._sleep(0.05)
        return self

    def _run(self) -> None:
        next_at = self._clock()
        while not self._stop.is_set() and self.elapsed < self._max:
            self.step(inline_request=False)
            if self._on_update is not None and self._clock() - self._last_update >= self._update_every:
                self._last_update = self._clock()
                try:
                    self._on_update(self.snapshot())
                except Exception:      # noqa: BLE001  (a failed save of progress must not stop the sampling)
                    pass
            next_at += self._interval
            wait = next_at - self._clock()
            if wait > 0:
                self._stop.wait(wait)
            else:
                next_at = self._clock()      # fell behind (a slow reader): do not try to catch up with a burst

    def stop(self, *, settle_s: float = 0.0) -> dict[str, Any]:
        """End sampling and return the finished timeline. ``settle_s`` keeps sampling a little longer first (the tail after recovery)."""
        if settle_s > 0 and self._thread is not None and self._thread.is_alive():
            self._stop.wait(settle_s)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self._interval * 4, 5.0))
        if self._request_thread is not None:
            self._request_thread.join(timeout=2.0)
        self.mark("end")
        return self.snapshot()
