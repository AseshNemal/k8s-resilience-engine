"""The measured timeline of a failure the developer causes themselves ("Watch a real failure").

A run of the validation loop has a thread that samples every couple of seconds. A watch has none: nothing runs in the background, and
the page asks for the status every few seconds. So the timeline of a watch gets ONE sample per status check, taken from the readings
that check already made, plus the optional extra readers (CPU and memory, warning events, pod logs, a real request to the
application). Two things keep that honest:

* a stretch in which nobody asked is not filled in. When the gap between two checks is longer than ``GAP_FACTOR`` intervals, an
  all-unknown sample is put in so the charts show an unknown stretch, never a state that was merely assumed to continue;
* a reader that cannot answer leaves its value ``None`` (and is counted in ``reads``), it never fails the watch.

The watch never knows when the developer stopped the node, only when it first SAW it not Ready: the mark is ``node-not-ready`` and
the summary dates the window from there. Nothing here writes to the cluster.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.engine.recommendation_evaluation_validation.monitoring.sampler import Readers, _code
from app.engine.recommendation_evaluation_validation.monitoring.timeline import Timeline

INTERVAL_S = 5.0          # how often the page asks; a gap is judged against it
GAP_FACTOR = 3.0
USAGE_EVERY = 3           # CPU and memory are read on every third check: each is one request per node
MIN_SPACING_S = 1.0       # two checks closer than this (two open tabs) are one measurement


def new_timeline(started: datetime, baseline: dict[str, int]) -> dict[str, Any]:
    timeline = Timeline(started_at=started.isoformat(), interval_s=INTERVAL_S, baseline={k: v for k, v in baseline.items() if v is not None})
    timeline.add_mark(0.0, "begin", "watching started")
    return timeline.to_dict()


def _try(timeline: Timeline, name: str, reader: Callable[..., Any] | None, *args: Any) -> Any:
    if reader is None:
        return None
    try:
        value = reader(*args)
    except Exception as error:      # noqa: BLE001  (a reader that cannot answer is a gap, never a failed watch)
        timeline.count_read(name, False, _code(error))
        return None
    timeline.count_read(name, True)
    return value


def record(data: dict[str, Any] | None, *, started: datetime, now: datetime, ready: dict[str, int | None] | None,
           nodes: dict[str, bool | None] | None, readers: Readers | None = None, since_iso: str | None = None,
           marks: list[tuple[str, str]] = ()) -> dict[str, Any]:
    """Add one sample to the timeline ``data`` (``None`` starts one) and return the new timeline. ``ready``/``nodes`` of ``None`` mean
    the cluster could not be read at this check: that is recorded as an unknown sample."""
    timeline = Timeline.from_dict(data) if data else Timeline(started_at=started.isoformat(), interval_s=INTERVAL_S)
    t = round(max((now - started).total_seconds(), 0.0), 2)
    last = timeline.samples[-1] if timeline.samples else None
    if last is not None and t - last["t"] < MIN_SPACING_S:
        return timeline.to_dict()
    if last is not None and t - last["t"] > GAP_FACTOR * timeline.interval_s:
        timeline.add_sample({"t": round(last["t"] + timeline.interval_s, 2), "ready": None, "nodes": None})
    sample: dict[str, Any] = {"t": t, "ready": ready, "nodes": nodes}
    for kind, detail in marks:
        timeline.add_mark(t, kind, detail)
    if readers is not None and ready is not None:
        if readers.usage is not None and sum(1 for s in timeline.samples if s.get("ready") is not None) % USAGE_EVERY == 0:
            usage = _try(timeline, "usage", readers.usage)
            if usage:
                sample["cpu"], sample["mem"] = dict(usage.get("cpu") or {}), dict(usage.get("mem") or {})
                if usage.get("source"):
                    timeline.sources["usage"] = usage["source"]
        elif last is not None and last.get("cpu") is not None:
            sample["cpu"], sample["mem"] = dict(last.get("cpu") or {}), dict(last.get("mem") or {})
        if readers.request is not None:
            sample["req"] = _try(timeline, "request", readers.request)
        for event in _try(timeline, "events", readers.events, t) or []:
            if since_iso is None or (event.get("at") or since_iso) >= since_iso:
                timeline.add_event({**event, "t": t})
        for pod, lines in _try(timeline, "logs", readers.logs) or []:
            if not any(entry["pod"] == pod for entry in timeline.logs):
                timeline.add_log(t, pod, lines)
    timeline.add_sample(sample)
    return timeline.to_dict()


def finish(data: dict[str, Any] | None, *, started: datetime, now: datetime) -> dict[str, Any] | None:
    """Close the recording with an ``end`` mark."""
    if not data:
        return data
    timeline = Timeline.from_dict(data)
    timeline.add_mark(max((now - started).total_seconds(), 0.0), "end")
    return timeline.to_dict()
