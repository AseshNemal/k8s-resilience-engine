"""One failure episode as the loop drives it: begin recording, mark what was done, then record the tail until things are back.

The loop calls ``begin`` before it asks for the stop (so the timeline holds a few "before" samples), ``mark`` at the moments it acts,
and ``finish`` after it has started the node again. ``finish`` keeps sampling until every service is back at its starting number of
ready replicas and every node reads Ready, or until ``tail_max_s`` has passed: a bound, so an application that never fully recovers
ends the episode with that fact on record instead of holding the run for ever.

Nothing here can change the cluster. A failure of the monitor itself (it could not start, or its thread died) must never fail or
alter the experiment: the loop treats a monitor that raises as "no timeline" and says so on the outcome.
"""

import time
from collections.abc import Callable
from typing import Any

from app.engine.recommendation_evaluation_validation.monitoring.sampler import TimelineSampler


def is_recovered(sample: dict[str, Any] | None, baseline: dict[str, int]) -> bool:
    """Every service that had ready replicas has as many again, and no node reads NotReady. Unknown is not recovered."""
    if not sample or not baseline:
        return False
    ready = sample.get("ready") or {}
    if any(ready.get(service) is None or ready[service] < count for service, count in baseline.items() if count > 0):
        return False
    return all(state is not False for state in (sample.get("nodes") or {}).values()) and sample.get("nodes") is not None


class EpisodeMonitor:
    def __init__(self, sampler: TimelineSampler, *, pre_roll: int = 3, tail_max_s: float = 60.0, tail_min_s: float = 4.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self._sampler, self._pre_roll, self._tail_max, self._tail_min = sampler, pre_roll, tail_max_s, tail_min_s
        self._clock, self._sleep = clock, sleep

    def begin(self) -> None:
        self._sampler.start(wait_samples=self._pre_roll)
        self._sampler.mark("begin")

    def mark(self, kind: str, detail: str = "") -> None:
        self._sampler.mark(kind, detail)

    def finish(self) -> dict[str, Any]:
        """Record the tail (until recovered, at most ``tail_max_s``) and return the finished timeline."""
        started = self._clock()
        while self._clock() - started < self._tail_max:
            timeline = self._sampler.snapshot()
            last = (timeline.get("samples") or [None])[-1]
            if is_recovered(last, timeline.get("baseline") or {}) and self._clock() - started >= self._tail_min:
                self._sampler.mark("settled", "every service is back at its starting number of ready replicas")
                break
            self._sleep(0.5)
        return self._sampler.stop()
