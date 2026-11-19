"""The measured timeline of a failure: the record, the sampler that fills it and the summary that reads it.

Everything runs on scripted readers and a fake clock; nothing here touches a cluster. What is pinned is the honesty of the
figures: an unreadable value stays unknown, a reader that raises is a counted gap, and a figure is only claimed from samples.
"""

import threading

import pytest

from app.engine.recommendation_evaluation_validation.monitoring.sampler import Readers, TimelineSampler
from app.engine.recommendation_evaluation_validation.monitoring.summary import compare_episodes, summarize
from app.engine.recommendation_evaluation_validation.monitoring.timeline import MAX_SAMPLES, Timeline, compact


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def scripted(script):
    """Readers that answer from ``script(t)`` -> dict with optional keys ready, nodes, req, usage."""
    clock = Clock()

    def pick(key):
        return lambda: (script(clock.now) or {}).get(key)
    readers = Readers(services=pick("ready"), nodes=pick("nodes"), request=pick("req"), usage=pick("usage"))
    return clock, readers


def run(sampler, clock, seconds, *, marks=()):
    pending = sorted(marks)
    while clock.now <= seconds:
        while pending and pending[0][0] <= clock.now:
            sampler.mark(pending[0][1], pending[0][2] if len(pending[0]) > 2 else "")
            pending.pop(0)
        sampler.step()
        clock.now += 1.0
    return sampler.snapshot()


def episode(t):
    """w1 stops at t=5: reviews loses a replica (3 -> 2), ratings goes down (1 -> 0) until t=15; the node is back at t=12."""
    down = 5 <= t < 12
    ready = {"reviews": 2 if down else 3, "ratings": 0 if 5 <= t < 15 else 1, "productpage": 1}
    ok = not (6 <= t < 10)
    return {"ready": ready, "nodes": {"w1": not (6 <= t < 12), "w2": True},
            "req": {"ok": ok, "ms": 40 if ok else None, "status": 200 if ok else 503, "why": None if ok else "HTTP 503"},
            "usage": {"cpu": {"w2": 100 + t * 10, "w1": None if 6 <= t < 12 else 50}, "mem": {"w2": 800.0}, "source": "kubelet-summary"}}


def build(script=episode, marks=((5, "stop", "w1"), (9, "restore", "w1"), (12, "node-ready", "w1")), until=20):
    clock, readers = scripted(script)
    sampler = TimelineSampler(readers, clock=clock, sleep=lambda s: None)
    return run(sampler, clock, until, marks=marks)


def test_summary_counts_downtime_and_detection_from_the_samples():
    summary = summarize(build())
    assert summary["evidence"] == "MEASURED"
    assert summary["stopIssuedAtS"] == 5.0
    assert summary["nodeNotReadyAfterS"] == pytest.approx(1.0)
    assert summary["servicesDown"] == ["ratings"]
    assert summary["servicesDegraded"] == ["reviews"]
    ratings = summary["services"]["ratings"]
    assert ratings["baseline"] == 1 and ratings["lowest"] == 0
    assert ratings["downS"] == pytest.approx(10.0)         # t=5..14, one second each
    assert ratings["backAtBaselineS"] == pytest.approx(10.0)
    assert summary["services"]["reviews"]["lowest"] == 2 and summary["services"]["reviews"]["downS"] == 0
    assert summary["windowS"] == pytest.approx(7.0)


def test_requests_report_outage_ratio_and_latency_separately():
    requests = summarize(build())["requests"]
    assert requests["total"] == 21 and requests["failed"] == 4
    assert requests["longestOutageS"] == pytest.approx(4.0)
    assert requests["firstFailureS"] == pytest.approx(1.0)
    assert requests["windowSuccessRatio"] == pytest.approx(1 - 4 / 8)      # t=5..12 inclusive is 8 requests
    assert requests["failureReasons"] == {"HTTP 503": 4}
    assert requests["latencyBeforeMs"]["p50"] == 40 and requests["latencyDuringMs"]["p95"] == 40


def test_recovery_is_measured_from_the_restore_mark_to_everything_back():
    assert summarize(build())["recoveryAfterRestoreS"] == pytest.approx(6.0)      # restore at 9; ratings is back at baseline at 15


def test_resources_keep_the_peak_and_leave_the_unread_node_unknown():
    resources = summarize(build())["resources"]
    assert resources["w2"]["cpuPeakMillicores"] == 280 and resources["w2"]["memoryPeakMiB"] == 800.0
    assert resources["w1"]["cpuPeakMillicores"] == 50
    assert resources["w1"]["readings"] < resources["w2"]["readings"]      # the stopped node has gaps, which are not zeros


def test_an_unreadable_value_is_a_gap_not_a_zero_or_a_pass():
    def script(t):
        return {"ready": None if 5 <= t < 8 else {"api": 1}, "nodes": {"w1": True}, "req": None}
    summary = summarize(build(script, marks=((4, "stop", "w1"),), until=12))
    assert summary["services"]["api"]["downS"] == 0 and summary["servicesDown"] == []
    assert summary["requests"] is None


def test_a_reader_that_raises_is_counted_and_never_stops_the_others():
    clock = Clock()

    class Boom(Exception):
        code = "BOOM"

    def services():
        raise Boom("no")
    sampler = TimelineSampler(Readers(services=services, nodes=lambda: {"w1": True}), clock=clock)
    sampler.step()
    snapshot = sampler.snapshot()
    assert snapshot["samples"][0]["nodes"] == {"w1": True} and snapshot["samples"][0]["ready"] is None
    assert snapshot["reads"]["services"] == {"ok": 0, "failed": 1, "last": "BOOM"}
    assert summarize(snapshot)["readerGaps"] == {"services": 1}


def test_the_baseline_is_what_was_ready_before_the_stop_and_not_what_was_left_after_it():
    clock, readers = scripted(lambda t: {"ready": {"a": 2} if t < 3 else {"a": 0}})
    sampler = TimelineSampler(readers, clock=clock)
    snapshot = run(sampler, clock, 6, marks=((3, "stop", "w1"),))
    assert snapshot["baseline"] == {"a": 2}


def test_the_sampler_marks_the_first_time_a_node_is_seen_not_ready():
    snapshot = build()
    seen = [m for m in snapshot["marks"] if m["kind"] == "node-not-ready"]
    assert [m["t"] for m in seen] == [6.0] and seen[0]["detail"] == "w1"


def test_a_long_recording_is_bounded_and_says_it_was_thinned():
    timeline = Timeline(started_at="x", interval_s=1.0)
    timeline.add_mark(50.0, "stop")
    for index in range(MAX_SAMPLES * 2):
        timeline.add_sample({"t": float(index)})
    assert len(timeline.samples) <= MAX_SAMPLES + 5 and timeline.truncated
    assert any(abs(s["t"] - 50.0) < 1e-9 for s in timeline.samples)      # the neighbourhood of a mark survives
    assert timeline.samples[-1]["t"] == MAX_SAMPLES * 2 - 1


def test_compact_keeps_the_newest_samples_and_all_the_marks():
    data = build()
    small = compact(data, last=5)
    assert len(small["samples"]) == 5 and small["samples"][-1] == data["samples"][-1] and small["marks"] == data["marks"]
    assert compact(None) is None


def test_comparing_two_episodes_claims_improvement_only_when_nothing_got_worse():
    before = summarize(build())

    def calmer(t):
        out = episode(t)
        out["ready"] = {"reviews": out["ready"]["reviews"], "ratings": 1, "productpage": 1}      # ratings no longer goes down
        out["req"] = {"ok": True, "ms": 40, "status": 200, "why": None}
        return out
    after = summarize(build(calmer))
    comparison = compare_episodes(before, after)
    by_id = {r["id"]: r for r in comparison["rows"]}
    assert comparison["improved"] is True
    assert by_id["servicesDown"]["verdict"] == "better" and by_id["longestOutage"]["verdict"] == "better"
    assert by_id["requestSuccessInWindow"]["after"] == 1.0
    worse = compare_episodes(after, before)
    assert worse["improved"] is False


def test_comparing_with_a_missing_side_is_unknown_not_better():
    comparison = compare_episodes(None, summarize(build()))
    assert comparison["improved"] is None and comparison["measuredBefore"] is False
    assert {r["verdict"] for r in comparison["rows"]} == {"unknown"}


def test_the_thread_samples_and_stop_returns_a_finished_timeline():
    ticks = threading.Event()
    seen = []

    def services():
        seen.append(1)
        if len(seen) >= 3:
            ticks.set()
        return {"web": 1}
    sampler = TimelineSampler(Readers(services=services), interval_s=0.05)
    sampler.start()
    assert ticks.wait(5.0)
    timeline = sampler.stop()
    assert timeline["marks"][-1]["kind"] == "end" and len(timeline["samples"]) >= 3
    assert timeline["baseline"] == {"web": 1}


def test_a_request_that_hangs_does_not_stop_the_readiness_samples():
    release = threading.Event()
    calls = []

    def hang():
        calls.append(1)
        release.wait(5.0)
        return {"ok": False, "ms": None, "status": None, "why": "timeout"}
    sampler = TimelineSampler(Readers(services=lambda: {"web": 1}, request=hang), interval_s=0.05)
    sampler.start()
    deadline = threading.Event()
    deadline.wait(0.5)
    taken = sampler.samples_taken
    release.set()
    timeline = sampler.stop()
    assert taken >= 4 and len(calls) == 1      # sampling went on; no second request was piled on the first
    assert len(timeline["samples"]) >= taken
