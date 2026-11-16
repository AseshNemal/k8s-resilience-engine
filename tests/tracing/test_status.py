import pytest

from app.integrations.tracing import TelemetryState, read_status
from app.integrations.tracing.status import excluded_services, newest_trace_start

from .fakes import NOW, FakeSource, trace_started


def status(source, **kwargs):
    return read_status(source, now=NOW, excluded=frozenset({"jaeger-all-in-one"}), **kwargs)


def test_an_unreachable_trace_service_is_reported_as_unknown_not_empty():
    answer = status(FakeSource(healthy=False))
    assert answer["query"]["reachable"] is False
    assert answer["telemetry"]["state"] == TelemetryState.UNREACHABLE.value
    assert answer["telemetry"]["serviceCount"] is None            # unknown, never 0


def test_a_service_that_stops_answering_midway_is_unreachable_not_empty():
    answer = status(FakeSource(services_fail=True))
    assert answer["query"]["reachable"] is False
    assert answer["telemetry"]["state"] == "UNREACHABLE"


def test_reachable_with_only_its_own_tracing_means_no_services_yet():
    answer = status(FakeSource(services=["jaeger-all-in-one"]))
    assert answer["query"]["reachable"] is True
    assert answer["telemetry"]["state"] == "NO_SERVICES"
    assert answer["telemetry"]["serviceCount"] == 0


def test_a_recent_trace_means_receiving_and_names_the_services():
    source = FakeSource(
        services=["jaeger-all-in-one", "productpage", "reviews"],
        traces={"productpage": [trace_started(30)], "reviews": [trace_started(90)]},
    )
    telemetry = status(source)["telemetry"]

    assert telemetry["state"] == "RECEIVING"
    assert telemetry["serviceCount"] == 2
    assert [s["name"] for s in telemetry["services"]] == ["productpage", "reviews"]
    assert telemetry["newestTraceAt"] == "2027-01-15T07:59:30Z"      # NOW - 30 s: the newer of the two services


def test_only_the_newest_trace_decides_freshness():
    source = FakeSource(services=["a", "b"], traces={"a": [trace_started(10_000)], "b": [trace_started(10)]})
    assert status(source)["telemetry"]["state"] == "RECEIVING"


def test_old_traces_are_stale_not_receiving():
    source = FakeSource(services=["a"], traces={"a": [trace_started(3600)]})
    assert status(source, fresh_seconds=300)["telemetry"]["state"] == "STALE"


def test_services_that_have_no_trace_in_the_lookback_are_stale_with_no_time():
    telemetry = status(FakeSource(services=["a"]))["telemetry"]
    assert telemetry["state"] == "STALE"
    assert telemetry["newestTraceAt"] is None
    assert telemetry["services"] == [{"name": "a", "lastTraceAt": None}]


def test_one_service_failing_to_answer_does_not_hide_the_others():
    source = FakeSource(services=["a", "b"], traces={"b": [trace_started(5)]}, fail_for={"a"})
    telemetry = status(source)["telemetry"]
    assert telemetry["state"] == "RECEIVING"
    assert {s["name"]: s["lastTraceAt"] is not None for s in telemetry["services"]} == {"a": False, "b": True}


def test_a_long_service_list_is_cut_and_says_so():
    names = [f"svc{i:02d}" for i in range(20)]
    telemetry = status(FakeSource(services=names), max_services=5)["telemetry"]
    assert telemetry["serviceCount"] == 20
    assert len(telemetry["services"]) == 5 and telemetry["truncated"] is True


def test_jaegers_own_services_are_excluded_whatever_their_case(monkeypatch):
    monkeypatch.delenv("TRACING_EXCLUDED_SERVICES", raising=False)
    assert {"jaeger-all-in-one", "jaeger-query", "jaeger"} <= excluded_services()
    answer = read_status(FakeSource(services=["Jaeger-All-In-One", "jaeger"]), now=NOW)
    assert answer["telemetry"]["state"] == "NO_SERVICES"
    assert excluded_services({"TRACING_EXCLUDED_SERVICES": " A , b "}) == {"a", "b"}


def test_status_only_reads_from_the_trace_service():
    source = FakeSource(services=["a"], traces={"a": [trace_started(1)]})
    status(source)
    assert set(source.calls) <= {"is_healthy", "get_services", "fetch_traces"}


@pytest.mark.parametrize(
    "traces, expected",
    [
        ([], None),
        ([{"spans": []}], None),
        ([{"spans": [{"startTime": True}]}], None),               # a boolean is not a time
        ([{"spans": [{"startTime": "5"}]}], None),
        ([{"spans": [{"startTime": 2_000_000}, {"startTime": 1_000_000}]}], 1.0),   # the trace began at its earliest span
        ([{"spans": [{"startTime": 1_000_000}]}, {"spans": [{"startTime": 9_000_000}]}], 9.0),
    ],
)
def test_the_newest_trace_start_is_read_only_from_real_span_times(traces, expected):
    assert newest_trace_start(traces) == expected
