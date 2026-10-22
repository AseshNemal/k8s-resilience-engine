"""The extension's read-only tracing routes. The trace service is a stand-in; no network or Docker is used."""

import pytest
from fastapi.testclient import TestClient

from extension import tracing_routes
from extension.asgi import app
from tests.tracing.fakes import FakeSource, trace_started
from app.integrations.tracing import status as tracing_status

BASE = "/_extension/tracing"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WORKFLOW_JAEGER_URL", "http://host.docker.internal:16686")
    monkeypatch.delenv("TRACING_OTLP_HTTP_ENDPOINT", raising=False)
    monkeypatch.delenv("TRACING_OTLP_GRPC_ENDPOINT", raising=False)
    monkeypatch.delenv("TRACING_EXCLUDED_SERVICES", raising=False)
    return TestClient(app)


def use(monkeypatch, source):
    monkeypatch.setattr(tracing_routes, "make_source", lambda: source)
    return source


def recent():
    # The routes use the real clock, so a trace "just now" is built from it.
    import time

    began = int((time.time() - 5) * 1_000_000)
    return {"traceID": "b" * 32, "spans": [{"startTime": began}]}


def test_the_connection_contract_names_the_query_and_otlp_endpoints(client):
    body = client.get(f"{BASE}/connection").json()
    assert body["query"] == {"url": "http://host.docker.internal:16686"}
    assert body["otlp"] == {"http": "http://host.docker.internal:4318", "grpc": "host.docker.internal:4317", "source": "same-host-standard-ports"}


def test_status_reports_real_incoming_telemetry(client, monkeypatch):
    use(monkeypatch, FakeSource(services=["jaeger-all-in-one", "productpage"], traces={"productpage": [recent()]}))
    body = client.get(f"{BASE}/status").json()
    assert body["query"]["reachable"] is True
    assert body["telemetry"]["state"] == "RECEIVING"
    assert [s["name"] for s in body["telemetry"]["services"]] == ["productpage"]


def test_status_of_an_unreachable_trace_service_is_a_normal_answer(client, monkeypatch):
    use(monkeypatch, FakeSource(healthy=False))
    response = client.get(f"{BASE}/status")
    assert response.status_code == 200
    assert response.json()["telemetry"]["state"] == "UNREACHABLE"


def test_applications_say_which_services_are_producing_traces(client, monkeypatch):
    use(monkeypatch, FakeSource(services=["productpage", "details.bookinfo"], traces={"productpage": [recent()], "details.bookinfo": [trace_started(10**7)]}))
    body = client.get(f"{BASE}/applications", params=[("namespace", "bookinfo"), ("service", "productpage"), ("service", "details"), ("service", "ratings")]).json()
    states = {row["service"]: row["state"] for row in body["services"]}
    assert states == {"productpage": "PRODUCING", "details": "STALE", "ratings": "NOT_SEEN"}
    assert (body["producing"], body["total"]) == (1, 3)


@pytest.mark.parametrize("params", [
    [("namespace", "Bad NS")],
    [("namespace", "ok"), ("service", "../etc")],
    [("namespace", "ok"), ("service", "A")],
])
def test_bad_names_are_a_coded_422_and_ask_nothing(client, monkeypatch, params):
    source = use(monkeypatch, FakeSource())
    response = client.get(f"{BASE}/applications", params=params)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "INVALID_NAME"
    assert source.calls == []


def test_a_missing_namespace_is_refused(client):
    assert client.get(f"{BASE}/applications").status_code == 422


def test_an_unreachable_trace_service_is_a_coded_503_for_applications(client, monkeypatch):
    use(monkeypatch, FakeSource(services_fail=True))
    response = client.get(f"{BASE}/applications", params={"namespace": "ns", "service": "a"})
    assert response.status_code == 503 and response.json()["detail"]["code"] == "TRACE_SERVICE_UNAVAILABLE"


def test_a_caller_cannot_choose_the_trace_service(client, monkeypatch):
    class Spy(FakeSource):
        def __init__(self):
            super().__init__(services=["a"], traces={"a": [recent()]})

    spy = Spy()
    use(monkeypatch, spy)
    client.get(f"{BASE}/status", params={"url": "http://attacker.example", "jaeger_url": "http://attacker.example"})
    assert spy.base_url == "http://jaeger.test:16686"          # the parameters are not read at all


def test_the_default_source_is_the_backends_own_setting(monkeypatch):
    monkeypatch.setenv("WORKFLOW_JAEGER_URL", "http://host.docker.internal:16686")
    source = tracing_routes.make_source()
    assert source.base_url == "http://host.docker.internal:16686"
    assert source.timeout == tracing_routes.QUERY_TIMEOUT_SECONDS


def test_the_routes_never_write(client):
    for path in ("/connection", "/status", "/applications"):
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(f"{BASE}{path}").status_code == 405


def test_the_status_route_uses_the_modules_state_names(client, monkeypatch):
    use(monkeypatch, FakeSource(services=["a"]))
    assert client.get(f"{BASE}/status").json()["telemetry"]["state"] in {s.value for s in tracing_status.TelemetryState}
