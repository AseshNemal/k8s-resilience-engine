"""An end-to-end probe of a workflow, recorded independently of the prediction and of pod readiness.

Component 3 says a workflow survives when its mandatory services still have a ready replica. That is a statement about the
cluster's state. A user does not see state; they send a request and get a page. Whether the two agree is exactly what a
validation should be able to show, so the probe asks the question the other way round: it sends real requests to the
workflow's entry point and judges what comes back.

Judging a response. A status code alone is not enough: an application can answer 200 and say, in the page, that a dependency
is down (Bookinfo's product page does this when reviews or details are unreachable). A sample therefore passes only when the
status is the expected one, every ``required_text`` is in the body, and no ``forbidden_text`` is. The reason a sample failed is
recorded with it.

Windows. The cluster is not in one state during a node failure, so the samples are grouped by the last thing that was done to
it before the sample was taken:

* ``before``: nothing has been done yet; the workflow should answer;
* ``detection``: the node was stopped, the control plane has not reacted. Requests may still go to pods that are gone. This is
  a real part of what a user experiences and is reported, but it is not what the model predicts;
* ``failure``: the node is NotReady and its pods are not ready. This is the state the prediction describes, and the only window
  that decides ``workflowSurvives``;
* ``recovery`` and ``after``: the node was started again.

Limits, stated with the data: sampling happens while the executor waits on the cluster, so its resolution is the poll interval;
a probe reaches the workflow through one entry point, so it shows that path works, not every path; and a probe on a test double
or a scripted fetch measures nothing about a real cluster.

This module sends nothing. The fetch is supplied by the caller, so a run can be tested without a network.
"""

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

PROBE_BASIS = ("End-to-end requests to the workflow's entry point, judged by status and page content, independent of pod "
               "readiness and of the prediction. Sampled while the executor waits on the cluster, so the resolution is the "
               "poll interval. It is a measurement only when run against a real cluster.")

# The instants the executor marks, in the order they happen, and the window that starts at each.
MARKS = ("stop-issued", "pods-unready", "restore-issued", "node-ready")
WINDOW_AFTER_MARK = {"stop-issued": "detection", "pods-unready": "failure", "restore-issued": "recovery", "node-ready": "after"}
WINDOWS = ("before", "detection", "failure", "recovery", "after")


@dataclass(frozen=True)
class ProbeSpec:
    workflow_id: str
    url: str
    method: str = "GET"
    expected_status: int = 200
    required_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()
    timeout_s: float = 3.0
    min_success_ratio: float = 1.0       # of the samples in the failure window, how many must pass for "survives"
    min_samples: int = 5                 # fewer samples in the failure window and survival is unknown, not guessed

    def __post_init__(self) -> None:
        if not self.url or not self.workflow_id:
            raise ValueError("a probe needs a workflow id and a URL")
        if self.method != "GET":
            raise ValueError("a probe only reads: GET")
        if not 0.0 < self.min_success_ratio <= 1.0:
            raise ValueError("min_success_ratio must be in (0, 1]")
        if self.min_samples < 1 or self.timeout_s <= 0:
            raise ValueError("min_samples must be at least 1 and timeout_s positive")


@dataclass(frozen=True)
class FetchResult:
    status: int | None
    body: str
    latency_ms: float | None
    error: str | None


@dataclass(frozen=True)
class ProbeSample:
    at_s: float                  # seconds on the injected clock
    wall: str                    # ISO time, for the record
    ok: bool
    status: int | None
    latency_ms: float | None
    error: str | None
    reason: str | None           # why a sample was judged a failure


def _judge(spec: ProbeSpec, fetched: FetchResult) -> str | None:
    """None when the response is what a working workflow returns; otherwise the reason it is not."""
    if fetched.error:
        return f"request failed: {fetched.error}"
    if fetched.status != spec.expected_status:
        return f"status {fetched.status}, expected {spec.expected_status}"
    # Forbidden text first: a page that says a dependency is down usually lacks the required text too, and the page's
    # own words are the more useful reason to keep.
    for text in spec.forbidden_text:
        if text in fetched.body:
            return f"page reports a failure: {text!r}"
    for text in spec.required_text:
        if text not in fetched.body:
            return f"required text missing: {text!r}"
    return None


class WorkflowProbe:
    def __init__(self, spec: ProbeSpec, fetch: Callable[[ProbeSpec], FetchResult], *,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.spec, self._fetch, self._clock, self._wall = spec, fetch, clock, wall
        self._samples: list[ProbeSample] = []
        self._marks: list[tuple[str, float, str]] = []

    def sample(self) -> ProbeSample:
        """One request. Never raises: a request that could not be made is a failed sample."""
        at = self._clock()
        try:
            fetched = self._fetch(self.spec)
        except Exception as error:      # noqa: BLE001
            fetched = FetchResult(None, "", None, type(error).__name__)
        reason = _judge(self.spec, fetched)
        sample = ProbeSample(at, self._wall().isoformat(), reason is None, fetched.status, fetched.latency_ms,
                             fetched.error, reason)
        self._samples.append(sample)
        return sample

    def mark(self, name: str) -> None:
        if name not in MARKS:
            raise ValueError(f"unknown mark {name!r}")
        self._marks.append((name, self._clock(), self._wall().isoformat()))

    def _window_of(self, sample: ProbeSample) -> str:
        window = "before"
        for name, at, _ in self._marks:
            if at <= sample.at_s:
                window = WINDOW_AFTER_MARK[name]
        return window

    def _summary(self, samples: list[ProbeSample]) -> dict[str, Any]:
        passed = [s for s in samples if s.ok]
        failed = [s for s in samples if not s.ok]
        latencies = [s.latency_ms for s in passed if s.latency_ms is not None]
        return {
            "samples": len(samples), "successes": len(passed), "failures": len(failed),
            "successRatio": (len(passed) / len(samples)) if samples else None,
            "firstFailureAtS": failed[0].at_s if failed else None,
            "statuses": dict(Counter(str(s.status) for s in samples)),
            "errors": dict(Counter(s.error for s in samples if s.error)),
            "reasons": dict(Counter(s.reason for s in failed if s.reason)),
            "meanLatencyMs": (sum(latencies) / len(latencies)) if latencies else None,
            "maxLatencyMs": max(latencies) if latencies else None,
        }

    def report(self) -> dict[str, Any]:
        grouped: dict[str, list[ProbeSample]] = {name: [] for name in WINDOWS}
        for sample in self._samples:
            grouped[self._window_of(sample)].append(sample)
        windows = {name: self._summary(samples) for name, samples in grouped.items()}
        failure = windows["failure"]
        survives: bool | None = None
        if failure["samples"] >= self.spec.min_samples:
            survives = failure["successRatio"] >= self.spec.min_success_ratio
        return {
            "spec": asdict(self.spec), "basis": PROBE_BASIS, "isMeasurement": False,
            "marks": [{"name": name, "atS": at, "wall": wall} for name, at, wall in self._marks],
            "windows": windows, "samples": [asdict(s) for s in self._samples], "sampleCount": len(self._samples),
            "workflowSurvives": survives,
        }
