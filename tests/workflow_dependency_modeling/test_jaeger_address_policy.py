"""Every route that takes a caller-supplied trace-server address applies the same allowlist as the POST routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.integrations.jaeger.client import DEFAULT_JAEGER_URL, JaegerClient
from app.main import app

client = TestClient(app)

ROUTES = [
    "/api/workflows/jaeger/status",
    "/api/workflows/jaeger/services",
    "/api/workflows/discover",
    "/api/workflows/live/evidence?operation=x",
    "/api/workflows/live",
]


@pytest.fixture(autouse=True)
def never_contact_a_trace_server(monkeypatch):
    """A rejected address must be refused before any network call, so any call at all fails the test."""
    def forbidden(*args, **kwargs):
        raise AssertionError("a trace server was contacted")

    for method in ("is_healthy", "get_services", "fetch_traces"):
        monkeypatch.setattr(JaegerClient, method, forbidden)
    monkeypatch.delenv("WORKFLOW_JAEGER_ALLOWED_HOSTS", raising=False)


def get(route: str, address: str):
    separator = "&" if "?" in route else "?"
    return client.get(f"{route}{separator}jaeger_url={address}")


@pytest.mark.parametrize("route", ROUTES)
def test_an_unapproved_host_is_refused_before_it_is_contacted(route):
    response = get(route, "http://169.254.169.254:80")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "JAEGER_ADDRESS_NOT_APPROVED"
    assert detail["retryable"] is False


def test_the_refusal_names_the_host_and_the_setting_that_approves_it():
    detail = get("/api/workflows/jaeger/status", "http://jaeger.local:16686").json()["detail"]
    assert "jaeger.local" in detail["message"]
    assert "WORKFLOW_JAEGER_ALLOWED_HOSTS" in detail["message"]


@pytest.mark.parametrize(
    "address,expected",
    [
        ("ftp://localhost:16686", "absolute HTTP or HTTPS"),
        ("http://user:secret@localhost:16686", "credentials"),
        ("http://localhost:16686?x=1", "query"),
    ],
)
def test_the_other_connection_rules_keep_their_messages(address, expected):
    response = get("/api/workflows/jaeger/status", address)
    assert response.status_code == 422
    assert expected in response.json()["detail"]["message"]
    assert "secret" not in response.text


def test_an_approved_host_is_checked(monkeypatch):
    monkeypatch.setattr(JaegerClient, "is_healthy", lambda self: True)
    body = get("/api/workflows/jaeger/status", "http://localhost:16686/").json()
    assert body == {"healthy": True, "jaegerUrl": "http://localhost:16686", "message": "Jaeger is reachable"}


def test_the_allowlist_setting_extends_the_approved_hosts(monkeypatch):
    monkeypatch.setenv("WORKFLOW_JAEGER_ALLOWED_HOSTS", "localhost,host.docker.internal")
    monkeypatch.setattr(JaegerClient, "is_healthy", lambda self: False)
    assert get("/api/workflows/jaeger/status", "http://host.docker.internal:16686").status_code == 200
    assert get("/api/workflows/jaeger/status", "http://jaeger:16686").status_code == 422


def test_the_servers_own_setting_and_an_empty_address_are_trusted_as_configured(monkeypatch):
    monkeypatch.setattr(JaegerClient, "is_healthy", lambda self: True)
    assert client.get("/api/workflows/jaeger/status").json()["jaegerUrl"] == DEFAULT_JAEGER_URL
    assert client.get("/api/workflows/jaeger/status", params={"jaeger_url": DEFAULT_JAEGER_URL}).status_code == 200
    assert client.get("/api/workflows/jaeger/status", params={"jaeger_url": ""}).status_code == 200
