"""The record of one failure episode: samples taken at intervals, marks for what was done, and the events seen on the way.

A ``Timeline`` is plain data. It is bounded: when it holds more than ``MAX_SAMPLES`` samples the oldest ones that are neither
a mark's neighbour nor an extreme are thinned out (``truncated`` says so), so a long episode can still be stored and drawn.
"""

from dataclasses import dataclass, field
from typing import Any

SCHEMA = "failure-timeline/v1"
MAX_SAMPLES = 600
MAX_EVENTS = 60
MAX_LOGS = 40
MAX_LOG_LINES = 12

# What a mark names. ``stop`` is when the stop was issued, ``restore`` when the start was issued; the others are marks the
# sampler or the loop adds from what it saw.
MARK_KINDS = frozenset({"begin", "stop", "node-not-ready", "pods-unready", "restore", "node-ready", "settled", "end"})


@dataclass
class Timeline:
    """Samples in order of time, with the marks that say what was happening. ``t`` is seconds since the recording began."""

    started_at: str
    interval_s: float
    baseline: dict[str, int] = field(default_factory=dict)      # ready replicas per service before anything was done
    samples: list[dict[str, Any]] = field(default_factory=list)
    marks: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    reads: dict[str, dict[str, int]] = field(default_factory=dict)    # reader -> {"ok": n, "failed": n, "last": code}
    sources: dict[str, str] = field(default_factory=dict)       # which source answered, for example "kubelet-summary"
    truncated: bool = False

    def add_sample(self, sample: dict[str, Any]) -> None:
        self.samples.append(sample)
        if len(self.samples) > MAX_SAMPLES:
            self._thin()

    def add_mark(self, t: float, kind: str, detail: str = "") -> None:
        self.marks.append({"t": round(t, 2), "kind": kind, "detail": detail})

    def add_event(self, event: dict[str, Any]) -> None:
        if len(self.events) < MAX_EVENTS and not any(e.get("key") == event.get("key") for e in self.events):
            self.events.append(event)

    def add_log(self, t: float, pod: str, lines: list[str]) -> None:
        if lines and len(self.logs) < MAX_LOGS:
            self.logs.append({"t": round(t, 2), "pod": pod, "lines": [line[:300] for line in lines[-MAX_LOG_LINES:]]})

    def count_read(self, reader: str, ok: bool, code: str | None = None) -> None:
        entry = self.reads.setdefault(reader, {"ok": 0, "failed": 0, "last": None})
        entry["ok" if ok else "failed"] += 1
        if not ok:
            entry["last"] = code

    def _thin(self) -> None:
        """Drop every second sample of the older half that is not next to a mark. Keeps the shape, bounds the size."""
        keep_near = [m["t"] for m in self.marks]
        half = len(self.samples) // 2
        older, newer = self.samples[:half], self.samples[half:]
        kept = [s for i, s in enumerate(older) if i % 2 == 0 or any(abs(s["t"] - t) <= 2 * self.interval_s for t in keep_near)]
        self.samples = kept + newer
        self.truncated = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Timeline":
        """A timeline read back from storage, so a recording can continue across requests."""
        return cls(started_at=data.get("startedAt") or "", interval_s=float(data.get("intervalS") or 1.0),
                   baseline=dict(data.get("baseline") or {}), samples=list(data.get("samples") or []), marks=list(data.get("marks") or []),
                   events=list(data.get("events") or []), logs=list(data.get("logs") or []),
                   reads={k: dict(v) for k, v in (data.get("reads") or {}).items()}, sources=dict(data.get("sources") or {}),
                   truncated=bool(data.get("truncated")))

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "evidence": "MEASURED", "startedAt": self.started_at, "intervalS": self.interval_s,
                "baseline": dict(self.baseline), "samples": list(self.samples), "marks": list(self.marks),
                "events": list(self.events), "logs": list(self.logs), "reads": {k: dict(v) for k, v in self.reads.items()},
                "sources": dict(self.sources), "truncated": self.truncated}


def compact(timeline: dict[str, Any] | None, *, last: int = 240) -> dict[str, Any] | None:
    """A smaller copy for a poll: the same fields, only the newest ``last`` samples. Marks, events and logs are kept whole."""
    if not timeline:
        return None
    samples = timeline.get("samples") or []
    if len(samples) <= last:
        return timeline
    return {**timeline, "samples": samples[-last:], "truncated": True}
