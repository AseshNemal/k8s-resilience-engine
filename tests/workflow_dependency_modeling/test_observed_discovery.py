"""Bounded, entry-service-agnostic discovery of observed workflow candidates."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.engine.workflow_dependency_modeling import analysis_session_service
from app.engine.workflow_dependency_modeling.auto_discovery import (
    discover_root_workflows,
    operation_key,
    select_workflow_traces,
)
from app.engine.workflow_dependency_modeling.observed_discovery import (
    DISCOVERED,
    INCOMPLETE_TRACES,
    JAEGER_UNAVAILABLE,
    NO_CANDIDATES,
    NO_TRACES,
    DiscoveryLimits,
    discover_observed_workflows,
)
from app.engine.workflow_dependency_modeling.trace_reader import parse_spans
from app.main import app


def make_trace(trace_id: str, spans: list[tuple], route: str | None = None, method: str = "GET") -> dict:
    """A Jaeger-shaped trace. ``spans`` are (span_id, parent_id, service, operation); the first is the root."""
    processes: dict[str, dict] = {}
    raw_spans = []
    for span_id, parent_id, service, operation in spans:
        processes.setdefault(service, {"serviceName": service, "tags": []})
        tags = []
        if parent_id is None and route is not None:
            tags = [
                {"key": "http.method", "type": "string", "value": method},
                {"key": "http.route", "type": "string", "value": route},
            ]
        raw_spans.append(
            {
                "traceID": trace_id,
                "spanID": span_id,
                "operationName": operation,
                "references": (
                    [{"refType": "CHILD_OF", "traceID": trace_id, "spanID": parent_id}] if parent_id else []
                ),
                "startTime": 1,
                "duration": 10,
                "tags": tags,
                "processID": service,
            }
        )
    return {"traceID": trace_id, "spans": raw_spans, "processes": processes}


def request_trace(trace_id: str, entry: str, route: str, *backends: str) -> dict:
    spans = [(f"{trace_id}-0", None, entry, "handle")]
    spans += [(f"{trace_id}-{i}", f"{trace_id}-0", name, f"call {name}") for i, name in enumerate(backends, 1)]
    return make_trace(trace_id, spans, route=route)


class FakeSource:
    """A trace source holding traces per service, like Jaeger: a trace is returned for every service it touches."""

    def __init__(self, traces: list[dict], services: list[str] | None = None) -> None:
        self.traces = traces
        self.services = services if services is not None else sorted(
            {name for trace in traces for name in (p["serviceName"] for p in trace["processes"].values())}
        )
        self.queries: list[tuple[str, int, str]] = []

    def list_services(self) -> list[str]:
        return self.services

    def fetch_workflow_traces(self, *, service_name, operation, lookback, limit):
        self.queries.append((service_name, limit, lookback))
        touching = [
            t for t in self.traces if service_name in {p["serviceName"] for p in t["processes"].values()}
        ]
        return touching[:limit]

    def fetch_trace(self, trace_id):  # pragma: no cover - discovery never reads by ID
        raise AssertionError("discovery must not fetch single traces")


class DownSource:
    def list_services(self):
        raise ConnectionError("Could not connect to Jaeger at http://secret-host:16686: refused")

    def fetch_workflow_traces(self, **_):  # pragma: no cover
        raise ConnectionError("down")


LIMITS = DiscoveryLimits()


def discover(source, limits: DiscoveryLimits = LIMITS, environ: dict | None = None) -> dict:
    return discover_observed_workflows(
        source, jaeger_url="http://jaeger:16686", lookback="1h", limits=limits, environ=environ or {}
    )


# --- states ---------------------------------------------------------------------------------------------------


def test_unreachable_backend_is_reported_without_raw_error_text() -> None:
    report = discover(DownSource())
    assert report["state"] == JAEGER_UNAVAILABLE
    assert report["retryable"] is True
    assert report["workflows"] == []
    assert "secret-host" not in str(report)


def test_connected_backend_with_no_services_has_no_traces() -> None:
    report = discover(FakeSource([], services=[]))
    assert report["state"] == NO_TRACES
    assert report["workflows"] == []


def test_connected_backend_with_services_but_no_traces_in_the_window() -> None:
    source = FakeSource([], services=["orders", "web"])
    report = discover(source)
    assert report["state"] == NO_TRACES
    assert report["servicesScanned"] == ["orders", "web"]


def test_real_traces_become_candidates_with_services_counts_and_root_operation() -> None:
    traces = [
        request_trace("t1", "web", "/orders", "orders", "billing"),
        request_trace("t2", "web", "/orders", "orders", "billing"),
        request_trace("t3", "web", "/orders", "orders"),
        request_trace("t4", "web", "/catalog", "catalog"),
    ]
    report = discover(FakeSource(traces))
    assert report["state"] == DISCOVERED
    assert report["tracesFetched"] == 4
    orders, catalog = report["workflows"]
    assert (orders["operationName"], orders["rootService"], orders["traceCount"]) == ("GET /orders", "web", 3)
    assert orders["participatingServices"] == ["billing", "orders", "web"]
    assert (catalog["operationName"], catalog["traceCount"]) == ("GET /catalog", 1)
    assert orders["evidenceStatus"] == "complete"
    assert orders["classificationStatus"] == "not_classified"
    assert orders["status"] == "candidate"
    assert orders["traceQuality"] == {"validCount": 3, "partialCount": 0, "invalidCount": 0}


@pytest.mark.parametrize("entry", ["frontend", "api-gateway", "istio-ingressgateway", "productpage"])
def test_any_entry_service_name_is_discovered(entry: str) -> None:
    report = discover(FakeSource([request_trace("t1", entry, "/home", "backend")]))
    assert report["state"] == DISCOVERED
    assert report["workflows"][0]["rootService"] == entry


def test_a_trace_seen_through_several_services_is_counted_once() -> None:
    # Jaeger returns this one trace for each of the three services it touches.
    source = FakeSource([request_trace("t1", "web", "/orders", "orders", "billing")])
    report = discover(source)
    assert [q[0] for q in source.queries] == ["billing", "orders", "web"]
    assert report["tracesFetched"] == 1
    assert report["workflows"][0]["traceCount"] == 1


def test_the_same_operation_under_two_entry_services_stays_two_candidates() -> None:
    traces = [request_trace("t1", "shop-web", "/", "shop-api"), request_trace("t2", "admin-web", "/", "admin-api")]
    report = discover(FakeSource(traces))
    assert sorted((w["rootService"], w["operationName"]) for w in report["workflows"]) == [
        ("admin-web", "GET /"),
        ("shop-web", "GET /"),
    ]


def test_only_health_and_telemetry_roots_leave_no_candidates() -> None:
    traces = [request_trace("t1", "web", "/healthz", "orders"), request_trace("t2", "web", "/metrics")]
    report = discover(FakeSource(traces))
    assert report["state"] == NO_CANDIDATES
    assert report["tracesFetched"] == 2


def _two_root_trace(trace_id: str) -> dict:
    # Two spans that each name a parent absent from the trace: propagation broke, so evidence is partial.
    trace = request_trace(trace_id, "web", "/orders", "orders")
    trace["spans"][1]["references"] = [{"refType": "CHILD_OF", "traceID": trace_id, "spanID": "missing"}]
    return trace


def test_traces_with_broken_propagation_are_reported_as_incomplete_not_discovered() -> None:
    report = discover(FakeSource([_two_root_trace("t1"), _two_root_trace("t2")]))
    assert report["state"] == INCOMPLETE_TRACES
    assert report["workflows"][0]["evidenceStatus"] == "unusable"
    assert report["workflows"][0]["traceQuality"]["validCount"] == 0
    assert report["tracesWithCompleteEvidence"] == 0


def test_a_mix_of_complete_and_broken_traces_is_discovered_but_marked_incomplete() -> None:
    traces = [request_trace("t1", "web", "/orders", "orders"), _two_root_trace("t2")]
    report = discover(FakeSource(traces))
    assert report["state"] == DISCOVERED
    workflow = report["workflows"][0]
    assert workflow["evidenceStatus"] == "incomplete"
    assert workflow["traceQuality"]["validCount"] == 1


def test_discovery_never_assigns_necessity_criticality_or_approval() -> None:
    workflow = discover(FakeSource([request_trace("t1", "web", "/orders", "orders")]))["workflows"][0]
    for forbidden in ("criticality", "mandatoryServices", "optionalServices", "approved", "dependencyClass"):
        assert forbidden not in workflow
    assert workflow["classificationStatus"] == "not_classified"


# --- bounds ---------------------------------------------------------------------------------------------------


def test_only_the_configured_number_of_services_is_scanned_and_the_rest_are_reported() -> None:
    traces = [request_trace(f"t{i}", f"svc-{i}", "/x") for i in range(5)]
    source = FakeSource(traces)
    report = discover(source, DiscoveryLimits(max_services=2, traces_per_service=10, max_traces=100))
    assert len(source.queries) == 2
    assert report["servicesScanned"] == ["svc-0", "svc-1"]
    assert report["servicesSkipped"] == ["svc-2", "svc-3", "svc-4"]
    assert report["truncated"] is True


def test_each_service_query_is_bounded_and_uses_the_requested_lookback() -> None:
    source = FakeSource([request_trace("t1", "web", "/x")])
    discover_observed_workflows(
        source,
        jaeger_url="http://jaeger:16686",
        lookback="6h",
        limits=DiscoveryLimits(max_services=3, traces_per_service=7, max_traces=20),
        environ={},
    )
    assert source.queries == [("web", 7, "6h")]


def test_a_full_page_from_a_service_marks_the_result_truncated() -> None:
    traces = [request_trace(f"t{i}", "web", "/x") for i in range(4)]
    report = discover(FakeSource(traces), DiscoveryLimits(max_services=5, traces_per_service=4, max_traces=100))
    assert report["truncated"] is True


def test_scanning_stops_once_the_total_trace_budget_is_reached() -> None:
    traces = [request_trace(f"a{i}", "alpha", "/a") for i in range(3)] + [
        request_trace(f"b{i}", "beta", "/b") for i in range(3)
    ]
    source = FakeSource(traces)
    report = discover(source, DiscoveryLimits(max_services=5, traces_per_service=3, max_traces=3))
    assert [q[0] for q in source.queries] == ["alpha"]
    assert report["servicesSkipped"] == ["beta"]
    assert report["tracesFetched"] == 3


def test_the_backends_own_services_are_not_scanned() -> None:
    source = FakeSource([request_trace("t1", "web", "/x")], services=["jaeger-all-in-one", "web"])
    discover(source)
    assert [q[0] for q in source.queries] == ["web"]


def test_excluded_services_are_configurable() -> None:
    source = FakeSource([request_trace("t1", "web", "/x")], services=["web", "noise"])
    discover(source, environ={"WORKFLOW_DISCOVERY_EXCLUDED_SERVICES": "noise"})
    assert [q[0] for q in source.queries] == ["web"]


def test_limits_come_from_settings_and_a_caller_cap_and_never_exceed_the_ceilings() -> None:
    limits = DiscoveryLimits.from_environment(
        {"WORKFLOW_DISCOVERY_MAX_SERVICES": "4", "WORKFLOW_DISCOVERY_TRACES_PER_SERVICE": "30"}, max_traces=20
    )
    assert limits.to_mapping() == {"maxServices": 4, "tracesPerService": 20, "maxTraces": 20}
    for bad in ({"WORKFLOW_DISCOVERY_MAX_SERVICES": "0"}, {"WORKFLOW_DISCOVERY_MAX_TRACES": "5001"}):
        with pytest.raises(ValueError):
            DiscoveryLimits.from_environment(bad)
    with pytest.raises(ValueError):
        DiscoveryLimits.from_environment({"WORKFLOW_DISCOVERY_MAX_SERVICES": "many"})


# --- no application-specific route assumptions in the generic path ----------------------------------------------


def test_generic_discovery_does_not_merge_routes_by_an_application_specific_pattern() -> None:
    traces = [make_trace(f"t{i}", [(f"s{i}", None, "web", "h")], route=f"/product/{i}") for i in range(2)]
    generic = discover(FakeSource(traces))
    assert sorted(w["operationName"] for w in generic["workflows"]) == ["GET /product/0", "GET /product/1"]


def test_the_benchmark_profile_still_merges_product_routes_and_both_names_select_the_traces() -> None:
    traces = [make_trace(f"t{i}", [(f"s{i}", None, "frontend", "h")], route=f"/product/{i}") for i in range(2)]
    assert {w.operation_name: w.trace_count for w in discover_root_workflows(traces)} == {"GET /product/{id}": 2}
    root = parse_spans(traces[:1])[0]
    assert operation_key(root) == "GET /product/{id}"
    assert operation_key(root, ()) == "GET /product/0"
    assert len(select_workflow_traces(traces, "GET /product/{id}")) == 2
    assert len(select_workflow_traces(traces, "GET /product/0")) == 1


# --- API --------------------------------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    def use(source):
        monkeypatch.setattr(
            analysis_session_service.JaegerTraceSource, "from_configuration", classmethod(lambda cls, c, **k: source)
        )
        return TestClient(app)

    monkeypatch.delenv("WORKFLOW_DISCOVERY_MAX_SERVICES", raising=False)
    monkeypatch.delenv("WORKFLOW_DISCOVERY_TRACES_PER_SERVICE", raising=False)
    monkeypatch.delenv("WORKFLOW_DISCOVERY_MAX_TRACES", raising=False)
    return use


def test_discovery_api_reports_candidates_for_an_approved_connection(client) -> None:
    source = FakeSource([request_trace("t1", "gateway", "/pay", "payments")])
    response = client(source).post("/api/workflows/discovery", json={"jaegerUrl": "http://localhost:16686"})
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == DISCOVERED
    assert body["workflows"][0]["rootService"] == "gateway"
    assert body["jaegerUrl"] == "http://localhost:16686"


def test_discovery_api_answers_200_with_a_state_when_the_trace_service_is_down(client) -> None:
    response = client(DownSource()).post("/api/workflows/discovery", json={"jaegerUrl": "http://localhost:16686"})
    assert response.status_code == 200
    assert response.json()["state"] == JAEGER_UNAVAILABLE


def test_discovery_api_limit_caps_the_total_traces_read(client) -> None:
    source = FakeSource([request_trace(f"t{i}", "web", "/x") for i in range(10)])
    body = client(source).post(
        "/api/workflows/discovery", json={"jaegerUrl": "http://localhost:16686", "limit": 3, "lookback": "30m"}
    ).json()
    assert body["limits"]["maxTraces"] == 3
    assert body["tracesFetched"] == 3
    assert source.queries[0] == ("web", 3, "30m")


@pytest.mark.parametrize(
    "payload",
    [
        {"jaegerUrl": "http://not-approved.example:16686"},
        {"jaegerUrl": "http://localhost:16686", "lookback": "forever"},
        {"jaegerUrl": "http://localhost:16686", "limit": 0},
        {"jaegerUrl": "http://localhost:16686", "limit": 5001},
        {},
    ],
)
def test_discovery_api_keeps_the_connection_and_bound_validation(client, payload) -> None:
    response = client(FakeSource([])).post("/api/workflows/discovery", json=payload)
    assert response.status_code == 422


# --- entry points from the call graph -----------------------------------------------------------------------------


class GraphSource(FakeSource):
    """A source that also knows which service calls which, as Jaeger's dependencies endpoint does."""

    def __init__(self, traces, edges, services=None, fail=False):
        super().__init__(traces, services)
        self.edges = edges
        self.fail = fail
        self.edge_requests: list[int] = []

    def list_call_edges(self, lookback_ms):
        self.edge_requests.append(lookback_ms)
        if self.fail:
            raise ConnectionError("dependencies endpoint not available")
        return self.edges


SHOP_EDGES = [("web", "orders"), ("web", "catalog"), ("orders", "billing"), ("batch", "catalog")]


def shop_traces():
    return (
        [request_trace(f"w{i}", "web", "/orders", "orders", "billing") for i in range(8)]
        + [request_trace(f"b{i}", "batch", "/nightly", "catalog") for i in range(2)]
    )


def test_only_entry_services_are_scanned_when_the_call_graph_is_known() -> None:
    source = GraphSource(shop_traces(), SHOP_EDGES)
    report = discover(source)
    assert sorted(q[0] for q in source.queries) == ["batch", "web"], "callees are inside their callers' traces"
    assert report["entryDetection"] == "call_graph"
    assert report["servicesCalledByOthers"] == ["billing", "catalog", "orders"]
    assert {w["rootService"] for w in report["workflows"]} == {"web", "batch"}
    assert report["servicesUnexamined"] == []


def test_the_call_graph_window_is_the_discovery_lookback_in_milliseconds() -> None:
    source = GraphSource(shop_traces(), SHOP_EDGES)
    discover_observed_workflows(
        source, jaeger_url="http://jaeger:16686", lookback="6h", limits=LIMITS, environ={}
    )
    assert source.edge_requests == [6 * 3_600_000]


def test_a_rare_entry_service_is_found_where_scanning_in_name_order_would_have_dropped_it() -> None:
    # "zeta" sorts last and its traces are never among the newest of the busy services it calls.
    traces = [request_trace(f"a{i}", "alpha", "/a", "shared") for i in range(60)] + [
        request_trace("z1", "zeta", "/nightly", "shared")
    ]
    edges = [("alpha", "shared"), ("zeta", "shared")]
    limits = DiscoveryLimits(max_services=2, traces_per_service=10, max_traces=100)
    report = discover(GraphSource(traces, edges), limits)
    assert {w["rootService"] for w in report["workflows"]} == {"alpha", "zeta"}
    assert {w["rootService"] for w in discover(FakeSource(traces), limits)["workflows"]} == {"alpha"}, (
        "without the call graph the same bound loses it"
    )


def test_entry_services_left_out_by_the_bound_are_named_as_unexamined_not_hidden() -> None:
    traces = [request_trace("a1", "alpha", "/a", "shared"), request_trace("z1", "zeta", "/z", "other")]
    edges = [("alpha", "shared"), ("alpha", "x"), ("zeta", "other")]
    report = discover(GraphSource(traces, edges), DiscoveryLimits(max_services=1, traces_per_service=5, max_traces=50))
    assert report["servicesScanned"] == ["alpha"]
    assert report["servicesSkipped"] == ["zeta"]
    assert report["servicesUnexamined"] == ["zeta"]
    assert report["truncated"] is True


def test_a_skipped_service_whose_traces_were_read_anyway_is_not_called_unexamined() -> None:
    traces = [request_trace("t1", "alpha", "/a", "beta")]
    # beta looks like an entry point (the graph is stale) but alpha's own traces already include it.
    report = discover(GraphSource(traces, [("alpha", "gamma"), ("x", "y")], services=["alpha", "beta"]),
                      DiscoveryLimits(max_services=1, traces_per_service=5, max_traces=50))
    assert "beta" in report["servicesSkipped"]
    assert report["servicesUnexamined"] == []


@pytest.mark.parametrize(
    "source",
    [
        GraphSource(shop_traces(), []),
        GraphSource(shop_traces(), SHOP_EDGES, fail=True),
        GraphSource(shop_traces(), [("ghost", "phantom")]),
    ],
    ids=["no edges known", "endpoint fails", "edges name no recorded service"],
)
def test_without_a_usable_call_graph_every_recorded_service_is_scanned(source) -> None:
    report = discover(source)
    assert report["entryDetection"] == "scan_all"
    assert report["state"] == DISCOVERED
    assert sorted(q[0] for q in source.queries) == ["batch", "billing", "catalog", "orders", "web"]
    assert report["servicesCalledByOthers"] == []


def test_a_failing_call_graph_does_not_make_the_trace_service_look_unavailable() -> None:
    assert discover(GraphSource(shop_traces(), SHOP_EDGES, fail=True))["state"] == DISCOVERED


def test_entry_detection_can_be_turned_off() -> None:
    source = GraphSource(shop_traces(), SHOP_EDGES)
    report = discover(source, environ={"WORKFLOW_DISCOVERY_ENTRY_DETECTION": "scan_all"})
    assert report["entryDetection"] == "scan_all"
    assert source.edge_requests == []


def test_the_fingerprint_is_stable_for_the_same_evidence_and_changes_with_it() -> None:
    first = discover(GraphSource(shop_traces(), SHOP_EDGES))["fingerprint"]
    again = discover(GraphSource(shop_traces(), SHOP_EDGES))["fingerprint"]
    more = discover(GraphSource(shop_traces() + [request_trace("w99", "web", "/orders", "orders", "billing")], SHOP_EDGES))
    assert first == again and len(first) == 16
    assert more["fingerprint"] != first


def test_a_hundred_traces_through_one_entry_are_read_once_not_once_per_service() -> None:
    backends = ["s1", "s2", "s3", "s4", "s5", "s6"]
    traces = [request_trace(f"t{i}", "web", "/x", *backends) for i in range(40)]
    edges = [("web", b) for b in backends]
    with_graph, without = GraphSource(traces, edges), FakeSource(traces)
    discover(with_graph)
    discover(without)
    assert len(with_graph.queries) == 1
    assert len(without.queries) == 7
