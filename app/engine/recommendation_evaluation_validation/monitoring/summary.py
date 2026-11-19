"""What a recorded timeline shows, counted and ordered. Pure: no cluster, no clock, no model.

Every figure is derived from samples the readers actually returned. A sample that could not be read is skipped, never treated as
healthy or as failed; when there is nothing to count, the figure is ``None``. Durations are attributed to a sample until the next
one (the last sample counts for one interval), so a duration is a measurement at the sampling resolution, and the summary says
what that resolution was.

Words used: the *window* is the stretch from the moment the stop was issued to the moment the node was Ready again. A service is
*down* in a sample when it had Ready replicas before and has none now, and *degraded* when it has fewer than before but not none.
"""

import math
from typing import Any


def _first_mark(timeline: dict[str, Any], kind: str) -> float | None:
    for mark in timeline.get("marks") or []:
        if mark.get("kind") == kind:
            return float(mark["t"])
    return None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def _durations(samples: list[dict[str, Any]], interval: float) -> list[float]:
    """How long each sample stands for: until the next sample, and one interval for the last one. Capped, so a long gap is not
    counted as continuous knowledge."""
    cap = max(interval * 3, 3.0)
    out = []
    for index, sample in enumerate(samples):
        nxt = samples[index + 1]["t"] if index + 1 < len(samples) else sample["t"] + interval
        out.append(min(max(nxt - sample["t"], 0.0), cap))
    return out


def _service_impact(samples: list[dict[str, Any]], durations: list[float], baseline: dict[str, int], stop_t: float | None) -> dict[str, Any]:
    impact: dict[str, Any] = {}
    for service, before in sorted(baseline.items()):
        if before <= 0:
            continue
        lowest: int | None = None
        down = degraded = 0.0
        first_below: float | None = None
        back_at: float | None = None
        for sample, length in zip(samples, durations):
            if stop_t is not None and sample["t"] < stop_t:
                continue
            ready = (sample.get("ready") or {}).get(service)
            if ready is None:
                continue
            lowest = ready if lowest is None else min(lowest, ready)
            if ready < before:
                first_below = sample["t"] if first_below is None else first_below
                back_at = None
                if ready == 0:
                    down += length
                else:
                    degraded += length
            elif first_below is not None and back_at is None:
                back_at = sample["t"]
        origin = stop_t or 0.0
        impact[service] = {"baseline": before, "lowest": lowest, "downS": round(down, 1), "degradedS": round(degraded, 1),
                           "firstBelowS": None if first_below is None else round(first_below - origin, 1),
                           "backAtBaselineS": None if back_at is None else round(back_at - origin, 1),
                           "wentDown": bool(lowest == 0), "lostReplicas": None if lowest is None else max(before - lowest, 0)}
    return impact


def _requests(samples: list[dict[str, Any]], durations: list[float], window: tuple[float | None, float | None], stop_t: float | None) -> dict[str, Any] | None:
    taken = [(s, d) for s, d in zip(samples, durations) if s.get("req")]
    if not taken:
        return None
    lo, hi = window

    def inside(t: float) -> bool:
        return lo is not None and t >= lo and (hi is None or t <= hi)

    ok = [s for s, _ in taken if s["req"].get("ok")]
    failed = [s for s, _ in taken if not s["req"].get("ok")]
    in_window = [s for s, _ in taken if inside(s["t"])]
    window_failed = [s for s in in_window if not s["req"].get("ok")]
    outside_ok_ms = [float(s["req"]["ms"]) for s, _ in taken if not inside(s["t"]) and s["req"].get("ok") and s["req"].get("ms") is not None]
    window_ok_ms = [float(s["req"]["ms"]) for s in in_window if s["req"].get("ok") and s["req"].get("ms") is not None]
    # The longest unbroken stretch of failed requests, from the first failure to the first success after it.
    longest = 0.0
    run_start: float | None = None
    first_fail: float | None = None
    for sample, _ in taken:
        if not sample["req"].get("ok"):
            run_start = sample["t"] if run_start is None else run_start
            first_fail = sample["t"] if first_fail is None else first_fail
        elif run_start is not None:
            longest = max(longest, sample["t"] - run_start)
            run_start = None
    if run_start is not None:
        longest = max(longest, taken[-1][0]["t"] + (durations[-1] if durations else 0.0) - run_start)
    reasons: dict[str, int] = {}
    for sample in failed:
        why = str(sample["req"].get("why") or sample["req"].get("status") or "failed")
        reasons[why] = reasons.get(why, 0) + 1
    return {
        "total": len(taken), "ok": len(ok), "failed": len(failed), "successRatio": round(len(ok) / len(taken), 4),
        "windowTotal": len(in_window), "windowFailed": len(window_failed),
        "windowSuccessRatio": None if not in_window else round((len(in_window) - len(window_failed)) / len(in_window), 4),
        "longestOutageS": round(longest, 1), "firstFailureS": None if first_fail is None or stop_t is None else round(first_fail - stop_t, 1),
        "latencyBeforeMs": {"p50": _percentile(outside_ok_ms, 0.5), "p95": _percentile(outside_ok_ms, 0.95)},
        "latencyDuringMs": {"p50": _percentile(window_ok_ms, 0.5), "p95": _percentile(window_ok_ms, 0.95)},
        "failureReasons": reasons,
    }


def _resources(samples: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    for sample in samples:
        for key, unit in (("cpu", "cpuPeakMillicores"), ("mem", "memoryPeakMiB")):
            for node, value in (sample.get(key) or {}).items():
                entry = nodes.setdefault(node, {"cpuPeakMillicores": None, "memoryPeakMiB": None, "readings": 0, "lastReadingS": None})
                if value is None:
                    continue
                entry[unit] = value if entry[unit] is None else max(entry[unit], value)
                if key == "cpu":
                    entry["readings"] += 1
                    entry["lastReadingS"] = sample["t"]
    return nodes


def summarize(timeline: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the timeline shows. ``None`` when there is no timeline at all."""
    if not timeline or not timeline.get("samples"):
        return None
    samples = timeline["samples"]
    interval = float(timeline.get("intervalS") or 1.0)
    durations = _durations(samples, interval)
    baseline = {k: v for k, v in (timeline.get("baseline") or {}).items() if v is not None}
    stop_t = _first_mark(timeline, "stop")
    restore_t, ready_t = _first_mark(timeline, "restore"), _first_mark(timeline, "node-ready")

    not_ready_t = _first_mark(timeline, "node-not-ready")
    if not_ready_t is None:
        for sample in samples:
            if stop_t is not None and sample["t"] >= stop_t and any(v is False for v in (sample.get("nodes") or {}).values()):
                not_ready_t = sample["t"]
                break
    if stop_t is None and not_ready_t is not None:
        stop_t = not_ready_t
    window_end = ready_t if ready_t is not None else samples[-1]["t"]
    impact = _service_impact(samples, durations, baseline, stop_t)
    requests = _requests(samples, durations, (stop_t, window_end), stop_t)

    recovery_s: float | None = None
    if restore_t is not None:
        for sample in samples:
            if sample["t"] < restore_t:
                continue
            ready = sample.get("ready") or {}
            all_back = all(ready.get(s) is not None and ready[s] >= b for s, b in baseline.items() if b > 0)
            node_up = all(v is not False for v in (sample.get("nodes") or {}).values())
            if baseline and all_back and node_up:
                recovery_s = round(sample["t"] - restore_t, 1)
                break
    down_services = sorted(s for s, v in impact.items() if v["wentDown"])
    degraded_services = sorted(s for s, v in impact.items() if not v["wentDown"] and (v["lostReplicas"] or 0) > 0)
    return {
        "evidence": "MEASURED", "samples": len(samples), "resolutionS": interval,
        "durationS": round(samples[-1]["t"] - samples[0]["t"], 1),
        "stopIssuedAtS": stop_t, "nodeNotReadyAfterS": None if not_ready_t is None or stop_t is None else round(not_ready_t - stop_t, 1),
        "windowS": None if stop_t is None else round(max(window_end - stop_t, 0.0), 1),
        "recoveryAfterRestoreS": recovery_s, "services": impact, "servicesDown": down_services,
        "servicesDegraded": degraded_services, "requests": requests, "resources": _resources(samples),
        "readerGaps": {name: counts["failed"] for name, counts in (timeline.get("reads") or {}).items() if counts.get("failed")},
        "events": len(timeline.get("events") or []), "truncated": bool(timeline.get("truncated")),
    }


# What the change is judged on when two episodes of the same node are put side by side. ``lower_is_better`` says which way is better.
_COMPARED = (
    ("requestSuccessInWindow", "Requests that succeeded while the node was down", "ratio", False,
     lambda s: ((s.get("requests") or {}).get("windowSuccessRatio"))),
    ("longestOutage", "Longest stretch with failing requests", "seconds", True,
     lambda s: ((s.get("requests") or {}).get("longestOutageS"))),
    ("servicesDown", "Services that lost every ready replica", "count", True, lambda s: len(s.get("servicesDown") or [])),
    ("serviceDownTime", "Total time a service had no ready replica", "seconds", True,
     lambda s: round(sum(v["downS"] for v in (s.get("services") or {}).values()), 1)),
    ("recovery", "Time for everything to be back after the node was started", "seconds", True,
     lambda s: s.get("recoveryAfterRestoreS")),
)


def compare_episodes(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """The same node's failure before and after a change, from two summaries. A side that was not measured stays unknown.

    ``verdict`` is "better", "worse", "same" or "unknown" per row; the overall ``improved`` is only claimed when no measured row got
    worse and at least one got better.
    """
    rows = []
    for key, label, unit, lower_is_better, read in _COMPARED:
        b = None if before is None else read(before)
        a = None if after is None else read(after)
        if b is None or a is None:
            verdict = "unknown"
        elif a == b:
            verdict = "same"
        else:
            verdict = "better" if (a < b) == lower_is_better else "worse"
        rows.append({"id": key, "label": label, "unit": unit, "before": b, "after": a, "verdict": verdict})
    verdicts = [r["verdict"] for r in rows]
    improved = None
    if "better" in verdicts or "worse" in verdicts:
        improved = "worse" not in verdicts
    return {"rows": rows, "improved": improved, "measuredBefore": before is not None, "measuredAfter": after is not None}
