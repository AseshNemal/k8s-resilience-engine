"""Tests for host-helper managed credentials: renewal state, safety rules and the ingest command.

As in test_cluster_connection.py the Kubernetes authorization check is stubbed; these tests assert
the rules around it: renewal timing from the token's real expiry, managed-mode restrictions,
clearing on disconnect or manual replace, and that no credential ever leaves the backend.
"""

import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from fastapi.testclient import TestClient

from extension import cluster_connection as cc
from extension import cluster_ingest
from extension.asgi import app

from .test_cluster_connection import TOKEN_MARKER, jwt, kubeconfig

HELPER_SERVER = "https://host.docker.internal:58227"
MANAGED = {"context": "kind-resilience-research"}
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def timed_jwt(*, issued: datetime, expires: datetime) -> str:
    """A token carrying both iat and exp, like a Kubernetes service-account token."""
    import base64

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'iat': int(issued.timestamp()), 'exp': int(expires.timestamp())})}.sig"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "KUBECONFIG_PATH", tmp_path / "kube" / "config")
    monkeypatch.setattr(cc, "_verify_access", lambda document: None)
    return TestClient(app)


def managed_put(client, token, *, server=HELPER_SERVER, managed=MANAGED):
    return client.put(
        "/_extension/cluster",
        json={"kubeconfig": kubeconfig(server=server, user={"token": token}), "managed": managed},
    )


def fresh_token(hours_left: float, lifetime_hours: float = 8):
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=hours_left)
    return timed_jwt(issued=expires - timedelta(hours=lifetime_hours), expires=expires)


# ---------------------------------------------------------------- renewal state from the real expiry


def test_a_fresh_managed_token_is_ok_and_reports_renew_at_four_hours_before_expiry(client):
    response = managed_put(client, fresh_token(hours_left=8))
    body = response.json()

    assert response.status_code == 200
    assert body["managed"] is True and body["context"] == "kind-resilience-research"
    assert body["state"] == "ok"
    expires = datetime.fromisoformat(body["expiresAt"].replace("Z", "+00:00"))
    renew_at = datetime.fromisoformat(body["renewAt"].replace("Z", "+00:00"))
    assert expires - renew_at == timedelta(hours=4)


def test_renewal_is_due_once_four_hours_remain(client):
    body = managed_put(client, fresh_token(hours_left=3.9)).json()

    assert body["state"] == "renew-soon"
    assert body["expired"] is False


def test_renewal_is_not_due_while_more_than_four_hours_remain(client):
    assert managed_put(client, fresh_token(hours_left=4.5)).json()["state"] == "ok"


def test_a_shorter_lived_token_renews_at_half_its_actual_lifetime_not_an_assumed_8_hours(client):
    # The API server clamped this token to 2 hours: renew with 1 hour left, not 4.
    body = managed_put(client, fresh_token(hours_left=1.5, lifetime_hours=2)).json()
    assert body["state"] == "ok"
    expires = datetime.fromisoformat(body["expiresAt"].replace("Z", "+00:00"))
    renew_at = datetime.fromisoformat(body["renewAt"].replace("Z", "+00:00"))
    assert expires - renew_at == timedelta(hours=1)

    assert managed_put(client, fresh_token(hours_left=0.9, lifetime_hours=2)).json()["state"] == "renew-soon"


def test_a_seven_day_token_renews_two_days_before_it_expires(client):
    # A long-lived token leaves a wide safety margin: a Mac that sleeps or a closed Docker Desktop for a
    # day or two must not be able to let it lapse before the next renewal attempt.
    body = managed_put(client, fresh_token(hours_left=3 * 24, lifetime_hours=7 * 24)).json()
    assert body["state"] == "ok"
    expires = datetime.fromisoformat(body["expiresAt"].replace("Z", "+00:00"))
    renew_at = datetime.fromisoformat(body["renewAt"].replace("Z", "+00:00"))
    assert expires - renew_at == timedelta(days=2)

    assert managed_put(client, fresh_token(hours_left=1.9 * 24, lifetime_hours=7 * 24)).json()["state"] == "renew-soon"


def test_an_expired_token_is_expired(client):
    body = managed_put(client, fresh_token(hours_left=-1)).json()

    assert body["state"] == "expired" and body["expired"] is True
    assert "renewAt" not in body


@pytest.mark.parametrize("hours_left, expected", [(72, "ok"), (24, "renew-soon")])
def test_without_a_readable_exp_claim_the_expiry_reported_by_kubernetes_is_used(client, hours_left, expected):
    # An opaque token has no exp or iat. The expiry the helper got from Kubernetes is used, and with no
    # known lifetime the default 2 day lead applies: 3 days left is fine, 1 day left is due.
    reported = (datetime.now(timezone.utc) + timedelta(hours=hours_left)).isoformat().replace("+00:00", "Z")
    response = client.put(
        "/_extension/cluster",
        json={
            "kubeconfig": kubeconfig(server=HELPER_SERVER, user={"token": "opaque-not-a-jwt"}),
            "managed": {**MANAGED, "expiresAt": reported},
        },
    )

    body = response.json()
    assert body["expiresAt"] == reported
    assert body["state"] == expected


def test_manual_connections_are_never_marked_for_automatic_renewal(client):
    token = fresh_token(hours_left=1)
    response = client.put("/_extension/cluster", json={"kubeconfig": kubeconfig(user={"token": token})})
    body = response.json()

    assert body["managed"] is False
    assert "context" not in body and "renewAt" not in body
    assert body["state"] == "ok"  # only expiry changes the state of a manual credential


# ---------------------------------------------------------------- managed-mode safety rules


def test_a_managed_connection_must_target_the_docker_desktop_host_gateway(client):
    response = managed_put(client, fresh_token(8), server="https://evil.example:6443")

    assert response.status_code == 422
    assert "host.docker.internal" in response.json()["detail"]
    assert client.get("/_extension/cluster").json() == {"connected": False}


def test_a_managed_connection_must_use_a_bearer_token(client):
    response = client.put(
        "/_extension/cluster",
        json={
            "kubeconfig": kubeconfig(
                server=HELPER_SERVER, user={"client-certificate-data": "Q0VSVA==", "client-key-data": "S0VZ"}
            ),
            "managed": MANAGED,
        },
    )

    assert response.status_code == 422
    assert "bearer token" in response.json()["detail"]


@pytest.mark.parametrize("context", ["", "a b", "x;rm -rf /", "../../etc", "n" * 129, "naïve"])
def test_a_hostile_or_malformed_context_name_is_rejected(client, context):
    response = managed_put(client, fresh_token(8), managed={"context": context})

    assert response.status_code == 422
    assert client.get("/_extension/cluster").json() == {"connected": False}


def test_the_read_only_check_still_applies_to_managed_credentials(client, monkeypatch):
    def refuse(document):
        raise cc._bad("This credential can change the cluster (create pods).")

    monkeypatch.setattr(cc, "_verify_access", refuse)
    response = managed_put(client, fresh_token(8))

    assert response.status_code == 422
    assert client.get("/_extension/cluster").json() == {"connected": False}


# ---------------------------------------------------------------- lifecycle


def test_disconnect_clears_the_managed_record_too(client, tmp_path):
    managed_put(client, fresh_token(8))
    assert (tmp_path / "kube" / "managed.json").is_file()

    assert client.delete("/_extension/cluster").json() == {"connected": False}
    assert not (tmp_path / "kube" / "managed.json").exists()
    assert not (tmp_path / "kube" / "config").exists()


def test_replacing_with_a_manual_upload_clears_the_managed_record(client):
    managed_put(client, fresh_token(8))
    client.put("/_extension/cluster", json={"kubeconfig": kubeconfig(user={"token": fresh_token(8)})})

    body = client.get("/_extension/cluster").json()
    assert body["managed"] is False and "context" not in body


@pytest.mark.skipif(os.name == "nt", reason="Windows has no Unix file modes")
def test_the_managed_record_holds_no_credential_and_is_private(client, tmp_path):
    token = fresh_token(8)
    managed_put(client, token)
    record = tmp_path / "kube" / "managed.json"

    assert oct(record.stat().st_mode & 0o777) == "0o600"
    assert token not in record.read_text(encoding="utf-8")
    assert json.loads(record.read_text(encoding="utf-8")) == {"context": "kind-resilience-research", "expiresAt": None}


def test_a_tampered_managed_record_is_ignored_not_trusted(client, tmp_path):
    managed_put(client, fresh_token(8))
    (tmp_path / "kube" / "managed.json").write_text('{"context": "x; rm -rf /"}')

    body = client.get("/_extension/cluster").json()
    assert body["managed"] is False and "context" not in body


# ---------------------------------------------------------------- no credential ever leaves


def test_status_never_contains_the_token(client):
    token = fresh_token(8)
    body = managed_put(client, token).json()
    status = client.get("/_extension/cluster").json()

    for payload in (body, status):
        assert token not in json.dumps(payload)
        assert "token" not in {key.lower() for key in payload}  # no field named token
    assert body["credential"] == "bearer token"  # the type is shown, never the value


def test_the_credential_is_not_logged_on_success_or_failure(client, caplog):
    token = fresh_token(8)
    with caplog.at_level(logging.DEBUG):
        managed_put(client, token)
        managed_put(client, token, server="https://evil.example:6443")
    assert token not in caplog.text and TOKEN_MARKER not in caplog.text


# ---------------------------------------------------------------- ingest command (docker exec transport)


def ingest(stdin_text, tmp_path, monkeypatch, argv=()):
    monkeypatch.setattr(cc, "KUBECONFIG_PATH", tmp_path / "kube" / "config")
    monkeypatch.setattr(cc, "_verify_access", lambda document: None)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    code = cluster_ingest.run(list(argv), io.StringIO(stdin_text))
    return code, json.loads(out.getvalue())


def test_ingest_installs_a_managed_credential_from_stdin(tmp_path, monkeypatch):
    token = fresh_token(8)
    request = {"kubeconfig": kubeconfig(server=HELPER_SERVER, user={"token": token}), "managed": MANAGED}
    code, result = ingest(json.dumps(request), tmp_path, monkeypatch)

    assert code == 0 and result["ok"] is True
    assert result["managed"] is True and result["state"] == "ok"
    assert token not in json.dumps(result)
    assert yaml.safe_load((tmp_path / "kube" / "config").read_text(encoding="utf-8"))["users"][0]["user"]["token"] == token


def test_ingest_applies_the_same_rules_as_the_http_route(tmp_path, monkeypatch):
    request = {"kubeconfig": kubeconfig(server="https://evil.example:6443", user={"token": fresh_token(8)}), "managed": MANAGED}
    code, result = ingest(json.dumps(request), tmp_path, monkeypatch)

    assert code == 1 and result["ok"] is False and result["status"] == 422
    assert "host.docker.internal" in result["error"]
    assert not (tmp_path / "kube" / "config").exists()


@pytest.mark.parametrize("junk", ["", "not json", "[]", '{"kubeconfig": 5}', "{}"])
def test_ingest_rejects_malformed_input_without_echoing_it(junk, tmp_path, monkeypatch):
    code, result = ingest(junk, tmp_path, monkeypatch)

    assert code == 1 and result["ok"] is False
    assert junk not in result["error"] or junk == ""


def test_ingest_reports_only_the_type_of_an_unexpected_error_never_the_secret(tmp_path, monkeypatch):
    secret = "tok-should-never-be-printed"

    def boom(*_args, **_kwargs):
        raise RuntimeError(f"failed with {secret}")

    monkeypatch.setattr(cc, "connect", boom)
    request = {"kubeconfig": kubeconfig(server=HELPER_SERVER, user={"token": secret}), "managed": MANAGED}
    code, result = ingest(json.dumps(request), tmp_path, monkeypatch)

    assert code == 1
    assert result["error"] == "unexpected error (RuntimeError)"
    assert secret not in json.dumps(result)


def test_ingest_refuses_oversized_input(tmp_path, monkeypatch):
    code, result = ingest("x" * (cluster_ingest.MAX_INPUT_BYTES + 10), tmp_path, monkeypatch)

    assert code == 1 and "too large" in result["error"]


def test_ingest_status_reports_without_a_credential(tmp_path, monkeypatch):
    code, result = ingest("", tmp_path, monkeypatch, argv=["--status"])

    assert code == 0 and result == {"ok": True, "connected": False}


def test_ingest_disconnect_removes_the_credential_and_the_managed_record(tmp_path, monkeypatch):
    request = {"kubeconfig": kubeconfig(server=HELPER_SERVER, user={"token": fresh_token(8)}), "managed": MANAGED}
    ingest(json.dumps(request), tmp_path, monkeypatch)
    assert (tmp_path / "kube" / "config").is_file() and (tmp_path / "kube" / "managed.json").is_file()

    code, result = ingest("", tmp_path, monkeypatch, argv=["--disconnect"])

    assert code == 0 and result == {"ok": True, "connected": False}
    assert not (tmp_path / "kube" / "config").exists() and not (tmp_path / "kube" / "managed.json").exists()


def test_ingest_disconnect_is_a_no_op_when_nothing_is_connected(tmp_path, monkeypatch):
    code, result = ingest("", tmp_path, monkeypatch, argv=["--disconnect"])

    assert code == 0 and result == {"ok": True, "connected": False}


def test_ingest_rejects_unknown_arguments(tmp_path, monkeypatch):
    code, result = ingest("", tmp_path, monkeypatch, argv=["--password", "x"])

    assert code == 2 and result["ok"] is False
