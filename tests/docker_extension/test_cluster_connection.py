"""Tests for the Docker Desktop extension's cluster connection endpoints.

No cluster is needed: the Kubernetes authorization check is replaced with a stub, which is what
lets these tests assert the rules (refuse write access, refuse unsafe kubeconfigs, never echo
or log credentials).
"""

import base64
import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from fastapi.testclient import TestClient

from extension import cluster_connection as cc
from extension.asgi import app

TOKEN_MARKER = "super-secret-token-value"


def kubeconfig(*, server="https://host.docker.internal:6443", cluster_extra=None, user=None, ca=True) -> str:
    cluster = {"server": server}
    if ca:
        cluster["certificate-authority-data"] = "Q0E="
    cluster.update(cluster_extra or {})
    return yaml.safe_dump(
        {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": "c",
            "contexts": [{"name": "c", "context": {"cluster": "k", "user": "u"}}],
            "clusters": [{"name": "k", "cluster": cluster}],
            "users": [{"name": "u", "user": user or {"token": TOKEN_MARKER}}],
        }
    )


def jwt(expires_at: datetime) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': int(expires_at.timestamp())})}.sig"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "KUBECONFIG_PATH", tmp_path / "kube" / "config")
    monkeypatch.setattr(cc, "_verify_access", lambda document: None)
    return TestClient(app)


def put(client, text):
    return client.put("/_extension/cluster", json={"kubeconfig": text})


def test_starts_disconnected(client):
    assert client.get("/_extension/cluster").json() == {"connected": False}


def test_accepts_a_valid_token_kubeconfig_and_reports_status(client):
    soon = datetime.now(timezone.utc) + timedelta(days=3)
    response = put(client, kubeconfig(user={"token": jwt(soon)}))

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["server"] == "host.docker.internal:6443"
    assert body["credential"] == "bearer token"
    assert body["expired"] is False
    assert body["expiresAt"].endswith("Z")


def test_an_expired_token_is_reported_as_expired(client):
    put(client, kubeconfig(user={"token": jwt(datetime.now(timezone.utc) - timedelta(hours=1))}))

    assert client.get("/_extension/cluster").json()["expired"] is True


@pytest.mark.skipif(os.name == "nt", reason="Windows has no Unix file modes")
def test_stored_file_is_private_and_minimal(client, tmp_path):
    put(client, kubeconfig(cluster_extra={"extensions": [{"name": "x"}]}))

    path = tmp_path / "kube" / "config"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(stored["users"][0]["user"]) == {"token"}
    assert set(stored["clusters"][0]["cluster"]) <= {"server", "certificate-authority-data", "tls-server-name"}


def test_responses_never_contain_the_credential(client):
    for text in (kubeconfig(), kubeconfig(server="http://insecure"), ":\n - ["):
        assert TOKEN_MARKER not in put(client, text).text
    assert TOKEN_MARKER not in client.get("/_extension/cluster").text


@pytest.mark.parametrize(
    "text, message",
    [
        (kubeconfig(server="http://insecure.example"), "https"),
        (kubeconfig(cluster_extra={"insecure-skip-tls-verify": True}), "TLS verification"),
        (kubeconfig(cluster_extra={"proxy-url": "http://proxy"}), "proxy-url"),
        (kubeconfig(ca=False), "certificate-authority-data"),
        (kubeconfig(user={"exec": {"command": "/bin/sh"}}), "bearer tokens"),
        (kubeconfig(user={"tokenFile": "/var/run/secrets/token"}), "bearer tokens"),
        (kubeconfig(user={"username": "admin", "password": "x"}), "bearer tokens"),
        (kubeconfig(user={"auth-provider": {"name": "gcp"}}), "bearer tokens"),
        ("not: a kubeconfig", "current-context"),
        (":\n - [", "valid YAML"),
        # An explicit id: pytest keeps the test id in an environment variable, and Windows refuses one that is
        # longer than 32767 characters, which this 70000-character value would make it.
        pytest.param("a: " + "x" * 70000, "too large", id="oversized-file"),
    ],
)
def test_unsafe_or_malformed_kubeconfigs_are_rejected(client, tmp_path, text, message):
    response = put(client, text)

    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert not (tmp_path / "kube" / "config").exists()


def test_client_certificate_credentials_are_accepted_when_embedded(client):
    user = {"client-certificate-data": "Q0VSVA==", "client-key-data": "S0VZ"}

    response = put(client, kubeconfig(user=user))

    assert response.status_code == 200
    assert response.json()["credential"] == "client certificate"


def test_disconnect_deletes_the_stored_credential(client, tmp_path):
    put(client, kubeconfig())

    assert client.delete("/_extension/cluster").json() == {"connected": False}
    assert not (tmp_path / "kube" / "config").exists()


class StubHttp:
    """Pretends to be an open connection to a cluster for a credential with given rights."""

    def __init__(self, allowed):
        self.allowed = allowed
        self.closed = False

    def close(self):
        self.closed = True


def verify_with(monkeypatch, allowed_pairs):
    http = StubHttp(allowed_pairs)
    monkeypatch.setattr(cc.rest, "connection_from_documents", lambda documents: object())
    monkeypatch.setattr(cc.rest, "RestClient", lambda connection, timeout: http)
    monkeypatch.setattr(cc, "_allowed", lambda http, verb, resource, group: (verb, resource) in http.allowed)
    try:
        return cc._verify_access({})
    finally:
        assert http.closed, "the connection must always be closed"


READS = {("list", "nodes"), ("list", "pods"), ("list", "deployments")}


def test_a_credential_that_can_read_what_the_app_reads_passes_verification(monkeypatch):
    verify_with(monkeypatch, READS)


@pytest.mark.parametrize("extra", [("create", "pods"), ("delete", "pods"), ("patch", "deployments"), ("get", "secrets")])
def test_a_credential_with_write_access_is_accepted_because_the_connected_cluster_is_full_access(monkeypatch, extra):
    verify_with(monkeypatch, READS | {extra})      # Apply and Reset need to write (developer-approved 2026-10-23)


def test_a_credential_missing_required_reads_is_refused(monkeypatch):
    with pytest.raises(cc.HTTPException) as refused:
        verify_with(monkeypatch, {("list", "nodes")})

    assert "cannot list nodes, pods and deployments" in refused.value.detail


# ---- the real request the verification makes ------------------------------------------------------

def open_with(handler):
    import httpx

    from app.integrations.kubernetes.rest import Connection, RestClient

    return RestClient(Connection(server="https://127.0.0.1:1", token="t"), 5, transport=httpx.MockTransport(handler))


def test_the_access_review_is_posted_to_the_authorization_api_with_the_right_attributes():
    import httpx

    sent = []

    def handler(request):
        sent.append((request.method, request.url.path, request.read()))
        return httpx.Response(201, json={"status": {"allowed": True}})

    assert cc._allowed(open_with(handler), "list", "deployments", "apps") is True

    import json

    method, path, body = sent[0]
    assert (method, path) == ("POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews")
    assert json.loads(body) == {
        "apiVersion": "authorization.k8s.io/v1",
        "kind": "SelfSubjectAccessReview",
        "spec": {"resourceAttributes": {"verb": "list", "resource": "deployments", "group": "apps"}},
    }


@pytest.mark.parametrize("reply", [
    {"status": {"allowed": False}}, {"status": {}}, {"status": {"allowed": "true"}}, {"status": None}, {}, [], "yes",
])
def test_anything_but_an_explicit_true_is_not_allowed(reply):
    import httpx

    assert cc._allowed(open_with(lambda request: httpx.Response(201, json=reply)), "get", "secrets", "") is False


class Failing:
    def __init__(self, error):
        self.error, self.closed = error, False

    def close(self):
        self.closed = True


@pytest.mark.parametrize(("error", "status", "words"), [
    (cc.rest.HttpStatusError(401), 401, "rejected the credential"),
    (cc.rest.HttpStatusError(403), 401, "rejected the credential"),
    (cc.rest.HttpStatusError(500), 502, "HTTP 500"),
    (cc.rest.TransportFailure("ConnectError"), 502, "ConnectError"),
])
def test_cluster_failures_while_verifying_are_reported_without_detail_and_the_connection_is_closed(
        monkeypatch, error, status, words):
    http = Failing(error)

    monkeypatch.setattr(cc.rest, "connection_from_documents", lambda documents: object())
    monkeypatch.setattr(cc.rest, "RestClient", lambda connection, timeout: http)

    def raise_it(*args):
        raise error

    monkeypatch.setattr(cc, "_allowed", raise_it)
    with pytest.raises(cc.HTTPException) as refused:
        cc._verify_access({})

    assert refused.value.status_code == status and words in refused.value.detail
    assert http.closed


def test_an_unusable_kubeconfig_is_a_422_that_names_the_problem(monkeypatch):
    def refuse(documents):
        raise cc.rest.KubeconfigError("the certificate authority or client certificate is not valid")

    monkeypatch.setattr(cc.rest, "connection_from_documents", refuse)
    with pytest.raises(cc.HTTPException) as refused:
        cc._verify_access({})

    assert refused.value.status_code == 422 and "certificate authority" in refused.value.detail
