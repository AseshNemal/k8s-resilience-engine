import pytest

from app.integrations.tracing import match_applications
from app.integrations.tracing.applications import MAX_SERVICES, jaeger_names_for

from .fakes import NOW, FakeSource, trace_started


def rows(answer):
    return {row["service"]: row for row in answer["services"]}


def test_a_service_with_a_fresh_trace_is_producing():
    source = FakeSource(services=["productpage"], traces={"productpage": [trace_started(20)]})
    answer = match_applications(source, "bookinfo", ["productpage"], now=NOW)
    assert rows(answer)["productpage"]["state"] == "PRODUCING"
    assert (answer["producing"], answer["total"]) == (1, 1)


def test_istio_style_names_with_the_namespace_match():
    source = FakeSource(services=["reviews.bookinfo"], traces={"reviews.bookinfo": [trace_started(5)]})
    row = rows(match_applications(source, "bookinfo", ["reviews"], now=NOW))["reviews"]
    assert row["state"] == "PRODUCING" and row["jaegerServices"] == ["reviews.bookinfo"]


def test_matching_ignores_case_but_nothing_else():
    assert jaeger_names_for("details", "bookinfo", ["Details", "details-v1", "my-details", "details.other", "details.bookinfo"]) == [
        "Details", "details.bookinfo",
    ]


def test_a_differently_named_service_is_not_seen_not_guessed():
    source = FakeSource(services=["productpage-v1"], traces={"productpage-v1": [trace_started(1)]})
    row = rows(match_applications(source, "bookinfo", ["productpage"], now=NOW))["productpage"]
    assert row["state"] == "NOT_SEEN" and row["jaegerServices"] == [] and row["lastTraceAt"] is None


def test_a_known_service_with_old_or_no_traces_is_stale():
    source = FakeSource(services=["a", "b"], traces={"a": [trace_started(7200)]})
    answer = rows(match_applications(source, "ns", ["a", "b"], now=NOW))
    assert answer["a"]["state"] == "STALE" and answer["b"]["state"] == "STALE"
    assert answer["b"]["lastTraceAt"] is None


def test_the_newest_of_several_matching_names_decides():
    source = FakeSource(
        services=["svc", "svc.ns"],
        traces={"svc": [trace_started(9000)], "svc.ns": [trace_started(10)]},
    )
    assert rows(match_applications(source, "ns", ["svc"], now=NOW))["svc"]["state"] == "PRODUCING"


def test_jaegers_own_services_never_count(monkeypatch):
    monkeypatch.delenv("TRACING_EXCLUDED_SERVICES", raising=False)
    source = FakeSource(services=["jaeger"], traces={"jaeger": [trace_started(1)]})
    assert rows(match_applications(source, "ns", ["jaeger"], now=NOW))["jaeger"]["state"] == "NOT_SEEN"


def test_duplicate_requested_names_are_answered_once():
    answer = match_applications(FakeSource(), "ns", ["a", "a"], now=NOW)
    assert answer["total"] == 1


@pytest.mark.parametrize("namespace, services", [
    ("Bad_NS", ["a"]), ("ns", ["UPPER"]), ("ns", ["a b"]), ("ns", ["../x"]), ("ns", ["a;b"]), ("", ["a"]),
    ("ns", [f"s{i}" for i in range(MAX_SERVICES + 1)]),
])
def test_names_that_are_not_kubernetes_names_are_refused_before_asking_anything(namespace, services):
    source = FakeSource()
    with pytest.raises(ValueError):
        match_applications(source, namespace, services, now=NOW)
    assert source.calls == []


def test_an_unreachable_trace_service_raises_instead_of_reporting_not_seen():
    with pytest.raises(ConnectionError):
        match_applications(FakeSource(services_fail=True), "ns", ["a"], now=NOW)
