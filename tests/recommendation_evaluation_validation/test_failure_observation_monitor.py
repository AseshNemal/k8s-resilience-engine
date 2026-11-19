"""The measured timeline of a watch: one sample per status check, gaps stay gaps, a reader that fails never fails the watch."""
from datetime import timedelta

import pytest

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.monitoring.sampler import Readers
from app.engine.recommendation_evaluation_validation.observation import FailureObservationService
from tests.recommendation_evaluation_validation.test_approval_api import sqlite_url  # noqa: F401
from tests.recommendation_evaluation_validation.test_failure_observation import Clock, World, clock, world  # noqa: F401


class Extras:
    def __init__(self):
        self.calls, self.url, self.fail = [], None, False
        self.requests_ok = True

    def __call__(self, namespace, request_url):
        self.calls.append((namespace, request_url))
        if self.fail:
            raise RuntimeError("no gateway")
        return Readers(
            usage=lambda: {"cpu": {"worker-1": 120, "worker-2": 80}, "mem": {"worker-1": 900, "worker-2": 700}, "source": "kubelet-summary"},
            events=lambda _t: [{"key": "ev-1", "reason": "NodeNotReady", "object": "node/worker-1", "message": "Node is not ready", "count": 1}],
            logs=lambda: [("details-0", ["connection refused"])],
            request=(lambda: {"ok": self.requests_ok, "ms": 42, "status": 200 if self.requests_ok else None, "why": None if self.requests_ok else "timeout"})
            if request_url else None)


@pytest.fixture
def extras():
    return Extras()


@pytest.fixture
def watching(sqlite_url, world, clock, extras):  # noqa: F811
    return FailureObservationService(get_engine(sqlite_url), world.overview, world.node_states, world.cluster_uid, clock=clock,
                                     monitor_readers=extras)


def test_every_check_adds_one_measured_sample_with_the_marks_of_what_was_seen(watching, world, clock):
    started = watching.start("bookinfo", None, request_url="http://127.0.0.1:8080/productpage")
    assert started["monitor"]["summary"] is not None and started["monitor"]["requests"] is True
    world.ready["worker-1"] = False
    world.replicas.update(details=0, reviews=2)
    clock.tick(5)
    watching.read(started["observationId"])
    world.ready["worker-1"] = True
    world.replicas.update(details=1, reviews=3)
    clock.tick(5)
    done = watching.read(started["observationId"])

    timeline = done["monitor"]["timeline"]
    assert [s["ready"]["details"] for s in timeline["samples"]] == [1, 0, 1]      # the first check at the start, the failure, the recovery
    marks = [m["kind"] for m in timeline["marks"]]
    assert marks == ["begin", "node-not-ready", "node-ready", "end"]
    summary = done["monitor"]["summary"]
    assert summary["servicesDown"] == ["details"] and summary["evidence"] == "MEASURED"
    assert timeline["events"][0]["reason"] == "NodeNotReady" and timeline["logs"][0]["pod"] == "details-0"
    assert timeline["sources"]["usage"] == "kubelet-summary"
    assert any(s.get("req", {}).get("ok") for s in timeline["samples"])


def test_without_extra_readers_availability_and_nodes_are_still_recorded(sqlite_url, world, clock):  # noqa: F811
    plain = FailureObservationService(get_engine(sqlite_url), world.overview, world.node_states, world.cluster_uid, clock=clock)
    started = plain.start("bookinfo", None)
    clock.tick(5)
    read = plain.read(started["observationId"])
    samples = read["monitor"]["timeline"]["samples"]
    assert len(samples) == 2 and samples[-1]["ready"] == {"details": 1, "reviews": 3}
    assert samples[-1]["nodes"] == {"worker-1": True, "worker-2": True, "worker-3": True}
    assert not any("cpu" in s or "req" in s for s in samples)
    assert read["monitor"]["requests"] is False


def test_a_stretch_nobody_asked_is_an_unknown_gap_not_an_assumed_state(watching, world, clock):
    started = watching.start("bookinfo", None)
    clock.tick(300)                                       # nobody asked for five minutes
    read = watching.read(started["observationId"])
    samples = read["monitor"]["timeline"]["samples"]
    assert [s["ready"] is None for s in samples] == [False, True, False]
    assert samples[1]["nodes"] is None


def test_an_unreadable_cluster_is_recorded_as_unknown(watching, world, clock):
    started = watching.start("bookinfo", None)
    world.unreadable = True
    clock.tick(5)
    read = watching.read(started["observationId"])
    last = read["monitor"]["timeline"]["samples"][-1]
    assert last["ready"] is None and last["nodes"] is None
    assert read["status"] == "WATCHING"


def test_two_checks_a_moment_apart_are_one_measurement(watching, clock):
    started = watching.start("bookinfo", None)
    clock.tick(0.4)
    watching.read(started["observationId"])
    watching.read(started["observationId"])
    assert len(watching.read(started["observationId"])["monitor"]["timeline"]["samples"]) == 1


def test_a_reader_that_cannot_be_built_does_not_stop_the_watch(watching, world, clock, extras):
    started = watching.start("bookinfo", None)
    extras.fail = True
    world.ready["worker-1"] = False
    clock.tick(5)
    read = watching.read(started["observationId"])
    assert read["status"] == "NODE_DOWN" and read["monitor"]["timeline"]["samples"][-1]["ready"]["details"] == 1
    assert "cpu" not in read["monitor"]["timeline"]["samples"][-1]


def test_stopping_closes_the_recording(watching, clock):
    started = watching.start("bookinfo", None)
    clock.tick(5)
    stopped = watching.stop(started["observationId"])
    assert stopped["monitor"]["timeline"]["marks"][-1]["kind"] == "end"


def test_the_history_list_keeps_the_summary_and_leaves_the_samples_out(watching, clock):
    started = watching.start("bookinfo", None)
    clock.tick(5)
    watching.stop(started["observationId"])
    row = watching.history(5)[0]
    assert row["monitor"]["summary"] is not None and row["monitor"]["timeline"] is None
