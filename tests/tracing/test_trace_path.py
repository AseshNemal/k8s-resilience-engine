"""verify_trace_path: which Bookinfo calls are *linked* in real traces.

The trace documents below are test inputs in Jaeger's JSON shape, built to exercise the parser. They are not
evidence about any cluster, and nothing at runtime builds one.
"""

import pytest

from app.integrations.tracing import PathVerdict, verify_trace_path
from app.integrations.tracing.trace_path import reviews_version_evidence, short_name, trace_edges

from .fakes import NOW, FakeSource

NS = "bookinfo"


def span(span_id, service, parent=None, tags=None, ref_type="CHILD_OF"):
    return {
        "spanID": span_id,
        "processID": service,
        "references": [{"refType": ref_type, "spanID": parent}] if parent else [],
        "tags": [{"key": key, "value": value} for key, value in (tags or {}).items()],
    }


def trace(*spans):
    services = {s["processID"] for s in spans}
    return {"traceID": "a" * 32, "spans": list(spans), "processes": {name: {"serviceName": name} for name in services}}


def full_path(reviews_tags=None):
    """client -> productpage -> details, productpage -> reviews -> ratings, as the sidecars would link them."""
    return trace(
        span("c1", "trace-demo-client.bookinfo"),
        span("p1", "productpage.bookinfo", "c1"),
        span("p2", "productpage.bookinfo", "p1"),
        span("d1", "details.bookinfo", "p2"),
        span("p3", "productpage.bookinfo", "p1"),
        span("r1", "reviews.bookinfo", "p3", reviews_tags if reviews_tags is not None else {"upstream_cluster": "inbound|9080||", "node_id": "sidecar~10.244.1.9~reviews-v2-6d4fc9b8c5-abcde.bookinfo~bookinfo.svc.cluster.local"}),
        span("r2", "reviews.bookinfo", "r1"),
        span("t1", "ratings.bookinfo", "r2"),
    )


def source(*traces):
    return FakeSource(services=["jaeger-all-in-one", "productpage.bookinfo", "details.bookinfo"], traces={"productpage.bookinfo": list(traces)})


def test_short_names_drop_the_namespace_forms_istio_uses():
    assert short_name("productpage.bookinfo", NS) == "productpage"
    assert short_name("Productpage.bookinfo.svc.cluster.local", NS) == "productpage"
    assert short_name("reviews", NS) == "reviews"
    assert short_name("reviews.other", NS) == "reviews.other"           # another namespace is not stripped


def test_a_correlated_trace_shows_every_expected_call():
    edges = trace_edges(full_path(), NS)
    assert edges == {
        ("trace-demo-client", "productpage"), ("productpage", "details"), ("productpage", "reviews"), ("reviews", "ratings"),
    }


def test_a_full_path_with_reviews_v2_is_verified():
    answer = verify_trace_path(source(full_path()), NS, now=NOW)
    assert answer["verdict"] == "VERIFIED"
    assert answer["completeTraces"] == 1 and answer["entryTraces"] == 1 and answer["missingEdges"] == []
    assert answer["reviewsVersion"] == {"v2": 1, "v1": 0, "unknown": 0}
    assert {(e["from"], e["to"]) for e in answer["edges"]} >= {("productpage", "details"), ("reviews", "ratings")}


def test_services_that_merely_appear_are_not_a_call_path():
    # Every service has spans in the trace, but no span refers to another: nothing is correlated.
    uncorrelated = trace(*(span(f"s{n}", name) for n, name in enumerate(
        ["productpage.bookinfo", "details.bookinfo", "reviews.bookinfo", "ratings.bookinfo"])))
    answer = verify_trace_path(source(uncorrelated), NS, now=NOW)
    assert answer["verdict"] == "PARTIAL"
    assert answer["edges"] == [] and answer["completeTraces"] == 0
    assert answer["missingEdges"] == ["productpage->details", "productpage->reviews", "reviews->ratings"]


def test_a_path_that_stops_at_reviews_is_partial_and_names_the_missing_call():
    v1 = trace(
        span("p1", "productpage.bookinfo"), span("d1", "details.bookinfo", "p1"),
        span("r1", "reviews.bookinfo", "p1", {"node_id": "sidecar~10.244.1.2~reviews-v1-abc.bookinfo~x"}),
    )
    answer = verify_trace_path(source(v1), NS, now=NOW)
    assert answer["verdict"] == "PARTIAL"
    assert answer["missingEdges"] == ["reviews->ratings"]
    assert answer["reviewsVersion"]["v1"] == 1


def test_one_reviews_v1_trace_stops_a_verified_verdict_because_routing_must_be_deterministic():
    v1 = trace(span("p1", "productpage.bookinfo"), span("r1", "reviews.bookinfo", "p1", {"upstream_cluster": "outbound|9080|v1|reviews.bookinfo.svc.cluster.local"}))
    answer = verify_trace_path(source(full_path(), v1), NS, now=NOW)
    assert answer["completeTraces"] == 1
    assert answer["verdict"] == "PARTIAL" and answer["reviewsVersion"]["v1"] == 1


def test_a_missing_parent_a_follows_from_link_and_a_same_service_link_are_not_edges():
    orphan = trace(span("a", "details.bookinfo", "not-in-this-trace"))
    follows = trace(span("p", "productpage.bookinfo"), span("d", "details.bookinfo", "p", ref_type="FOLLOWS_FROM"))
    inside = trace(span("p", "productpage.bookinfo"), span("q", "productpage.bookinfo", "p"))
    for t in (orphan, follows, inside):
        assert trace_edges(t, NS) == set()


def test_malformed_spans_are_skipped_not_guessed():
    broken = {"spans": [{"noID": 1}, "text", {"spanID": "x", "processID": "missing", "references": [{"refType": "CHILD_OF", "spanID": "x"}]}], "processes": {}}
    assert trace_edges(broken, NS) == set()
    assert trace_edges({}, NS) == set()


def test_version_evidence_is_read_from_tag_values_and_v1_outranks_v2():
    tags = lambda **kw: trace(span("p", "productpage.bookinfo"), span("r", "reviews.bookinfo", "p", kw))
    assert reviews_version_evidence(tags(a="reviews-v2-xyz"), NS) == "v2"
    assert reviews_version_evidence(tags(a="outbound|9080|v2|reviews.bookinfo.svc.cluster.local"), NS) == "v2"
    assert reviews_version_evidence(tags(a="reviews-v1-x", b="reviews-v2-y"), NS) == "v1"
    assert reviews_version_evidence(tags(a="something else", b=5), NS) == "unknown"
    # Tags on other services' spans say nothing about the reviews version.
    other = trace(span("p", "productpage.bookinfo", None, {"a": "reviews-v1-x"}), span("r", "reviews.bookinfo", "p"))
    assert reviews_version_evidence(other, NS) == "unknown"


def test_no_productpage_traces_is_no_traces_not_success():
    assert verify_trace_path(FakeSource(services=["details.bookinfo"]), NS, now=NOW)["verdict"] == "NO_TRACES"
    assert verify_trace_path(source(), NS, now=NOW)["verdict"] == "NO_TRACES"
    answer = verify_trace_path(FakeSource(), NS, now=NOW)
    assert answer["tracesExamined"] == 0 and answer["missingEdges"]


def test_an_unreachable_trace_service_is_reported_as_such():
    assert verify_trace_path(FakeSource(services_fail=True), NS, now=NOW)["verdict"] == PathVerdict.UNREACHABLE.value
    failing = FakeSource(services=["productpage.bookinfo"], fail_for={"productpage.bookinfo"})
    assert verify_trace_path(failing, NS, now=NOW)["verdict"] == "UNREACHABLE"


def test_the_number_of_traces_read_is_bounded():
    asked = []

    class Spy(FakeSource):
        def fetch_traces(self, service_name, operation=None, limit=100, lookback="1h", tags=None):
            asked.append(limit)
            return super().fetch_traces(service_name, operation, limit, lookback, tags)

    verify_trace_path(Spy(services=["productpage.bookinfo"]), NS, limit=10_000, now=NOW)
    assert asked == [100]


def test_only_reads_are_made_and_a_bad_namespace_asks_nothing():
    probe = source(full_path())
    verify_trace_path(probe, NS, now=NOW)
    assert set(probe.calls) <= {"get_services", "fetch_traces"}
    untouched = FakeSource()
    with pytest.raises(ValueError):
        verify_trace_path(untouched, "Bad NS", now=NOW)
    assert untouched.calls == []
