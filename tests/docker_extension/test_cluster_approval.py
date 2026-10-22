"""The backend's record of the one cluster a person approved, and the rules for connecting without asking.

A first connection (origin "authorize") is the person's explicit approval: it records the cluster's identity.
A renewal or a reconnect (origin "renew"/"reconnect") asks nobody, so the backend only accepts it for that same
identity and never after the person disconnected. The disconnect is kept in the data volume, so it survives page
reloads, backend restarts and the background renewal job. As in the other extension tests, the Kubernetes
authorization check is stubbed.
"""

import fcntl
import io
import json
import logging
import os
import stat

import pytest
from fastapi.testclient import TestClient

from extension import cluster_connection as cc
from extension import cluster_ingest
from extension.asgi import app

from .test_cluster_connection import TOKEN_MARKER, kubeconfig
from .test_cluster_managed import HELPER_SERVER, fresh_token, managed_put

CONTEXT = "docker-desktop"
IDENTITY = {"server": "127.0.0.1:50345", "caSha256": "ab" * 32}
OTHER_CA = {"server": "127.0.0.1:50345", "caSha256": "cd" * 32}
OTHER_PORT = {"server": "127.0.0.1:60000", "caSha256": "ab" * 32}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "KUBECONFIG_PATH", tmp_path / "kube" / "config")
    monkeypatch.setattr(cc, "_verify_read_only", lambda document: None)
    return TestClient(app)


def install(client, origin="authorize", identity=IDENTITY, context=CONTEXT, token=None):
    managed = {"context": context, "origin": origin}
    if identity is not None:
        managed["identity"] = identity
    return managed_put(client, token or fresh_token(hours_left=8), managed=managed)


def status(client):
    return client.get("/_extension/cluster").json()


# ---------------------------------------------------------------- first-time approval is recorded

def test_a_first_authorization_records_the_approved_identity(client):
    response = install(client)

    assert response.status_code == 200
    approved = response.json()["approved"]
    assert approved == {"context": CONTEXT, **IDENTITY, "disconnectedByUser": False}


def test_nothing_is_approved_until_a_person_authorizes(client):
    assert status(client) == {"connected": False}


def test_the_record_is_private_and_holds_no_credential(client, tmp_path):
    install(client, token=TOKEN_MARKER + ".x.y")

    record = tmp_path / "kube" / "approval.json"
    assert stat.S_IMODE(os.stat(record).st_mode) == 0o600
    text = record.read_text()
    assert TOKEN_MARKER not in text and "token" not in text.lower()
    assert json.loads(text)["context"] == CONTEXT


def test_an_authorization_without_an_identity_records_nothing_to_reconnect_to(client):
    install(client, identity=None)

    assert "approved" not in status(client)


def test_malformed_identities_are_refused_and_nothing_is_stored(client, tmp_path):
    for bad in ({"server": "not a host!", "caSha256": "ab" * 32}, {"server": "127.0.0.1:1", "caSha256": "xyz"},
                {"server": "127.0.0.1:1", "caSha256": "AB" * 32}, {"server": "", "caSha256": "ab" * 32}):
        assert install(client, identity=bad).status_code == 422
    assert not (tmp_path / "kube" / "config").exists() and not (tmp_path / "kube" / "approval.json").exists()


# ---------------------------------------------------------------- disconnecting is remembered

def test_a_disconnect_keeps_the_approval_but_marks_it_disconnected_by_the_person(client):
    install(client)

    after = client.delete("/_extension/cluster").json()

    assert after["connected"] is False
    assert after["approved"] == {"context": CONTEXT, **IDENTITY, "disconnectedByUser": True}


def test_the_disconnect_survives_a_restart_of_the_backend_and_the_ingest_process(client, tmp_path, monkeypatch):
    install(client)
    client.delete("/_extension/cluster")

    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    cluster_ingest.run(["--status"], io.StringIO(""))      # another process, as the helper's transport runs it

    assert json.loads(out.getvalue())["approved"]["disconnectedByUser"] is True


def test_removing_access_through_the_helper_is_also_a_disconnect_the_person_chose(client, monkeypatch):
    install(client)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)

    cluster_ingest.run(["--disconnect"], io.StringIO(""))

    assert json.loads(out.getvalue())["approved"]["disconnectedByUser"] is True


@pytest.mark.parametrize("origin", ["renew", "reconnect"])
def test_a_renewal_or_reconnect_that_arrives_after_a_disconnect_is_refused_and_stores_nothing(client, tmp_path, origin):
    install(client)
    client.delete("/_extension/cluster")                   # the person clicked Disconnect while a renewal was running

    response = install(client, origin=origin)

    assert response.status_code == 409 and "disconnected" in response.json()["detail"].lower()
    assert not (tmp_path / "kube" / "config").exists()
    assert status(client)["connected"] is False


def test_a_new_explicit_authorization_clears_the_disconnect(client):
    install(client)
    client.delete("/_extension/cluster")

    install(client, origin="authorize")

    assert status(client)["approved"]["disconnectedByUser"] is False
    assert status(client)["connected"] is True


def test_a_manual_connection_neither_creates_nor_clears_an_approval(client):
    install(client)
    client.delete("/_extension/cluster")

    manual = client.put("/_extension/cluster", json={"kubeconfig": kubeconfig(server=HELPER_SERVER)})

    assert manual.status_code == 200
    assert status(client)["approved"]["disconnectedByUser"] is True


# ---------------------------------------------------------------- reconnecting and renewing without asking

def test_a_reconnect_is_accepted_only_for_the_approved_identity(client):
    install(client)
    client.delete("/_extension/cluster")
    client.put("/_extension/cluster", json={"kubeconfig": kubeconfig(server=HELPER_SERVER)})   # leaves the marker
    # a person who then approves again, and later loses the connection without disconnecting, is reconnected
    install(client, origin="authorize")
    cc.KUBECONFIG_PATH.unlink()
    cc._managed_path().unlink()

    response = install(client, origin="reconnect")

    assert response.status_code == 200 and response.json()["connected"] is True


@pytest.mark.parametrize("identity", [OTHER_CA, OTHER_PORT])
@pytest.mark.parametrize("origin", ["renew", "reconnect"])
def test_a_renewal_or_reconnect_for_a_different_cluster_is_refused(client, tmp_path, origin, identity):
    install(client)
    before = (tmp_path / "kube" / "config").read_text()

    response = install(client, origin=origin, identity=identity)

    assert response.status_code == 409 and "approved" in response.json()["detail"].lower()
    assert (tmp_path / "kube" / "config").read_text() == before


def test_a_renewal_for_a_different_context_is_refused(client):
    install(client)

    assert install(client, origin="renew", context="kind-other").status_code == 409


def test_a_reconnect_with_nothing_approved_is_refused(client):
    response = install(client, origin="reconnect")

    assert response.status_code == 409 and "approved" in response.json()["detail"].lower()
    assert status(client) == {"connected": False}


def test_a_reconnect_must_carry_an_identity(client):
    install(client)
    cc.KUBECONFIG_PATH.unlink()
    cc._managed_path().unlink()

    assert install(client, origin="reconnect", identity=None).status_code == 422


def test_a_connection_from_before_identities_were_recorded_renews_once_and_records_its_identity(client):
    client.put("/_extension/cluster", json={"kubeconfig": kubeconfig(server=HELPER_SERVER, user={"token": fresh_token(1)}),
                                            "managed": {"context": CONTEXT}})    # an old helper: no origin, no identity
    assert "approved" not in status(client)

    response = install(client, origin="renew")

    assert response.status_code == 200
    assert response.json()["approved"]["server"] == IDENTITY["server"]


def test_a_legacy_renewal_is_not_accepted_for_a_context_that_is_not_connected(client):
    assert install(client, origin="renew").status_code == 409


def test_unknown_origins_are_refused(client):
    assert install(client, origin="anything-else").status_code == 422


# ---------------------------------------------------------------- one connection at a time

def hold_the_connection_lock(tmp_path):
    (tmp_path / "kube").mkdir(parents=True, exist_ok=True)
    handle = open(tmp_path / "kube" / ".connect.lock", "w")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def test_a_second_connection_attempt_is_refused_at_once_while_one_is_running(client, tmp_path):
    import time

    handle = hold_the_connection_lock(tmp_path)
    try:
        started = time.monotonic()
        response = install(client)
        elapsed = time.monotonic() - started
    finally:
        handle.close()

    assert response.status_code == 409 and "already" in response.json()["detail"].lower()
    assert elapsed < 0.15, "a connection attempt must be refused at once, not queued behind the running one"
    assert not (tmp_path / "kube" / "config").exists()


def test_the_lock_file_is_private_and_is_released_after_a_success_and_after_a_refusal(client, tmp_path):
    assert install(client).status_code == 200
    assert stat.S_IMODE(os.stat(tmp_path / "kube" / ".connect.lock").st_mode) == 0o600
    assert install(client, origin="renew", identity=OTHER_CA).status_code == 409
    handle = hold_the_connection_lock(tmp_path)            # would raise if either attempt had kept the lock
    handle.close()


def test_a_disconnect_waits_for_a_running_attempt_only_a_short_while_then_says_so(client, tmp_path, monkeypatch):
    install(client)
    monkeypatch.setattr(cc, "DISCONNECT_WAIT_SECONDS", 0.2)
    handle = hold_the_connection_lock(tmp_path)
    try:
        response = client.delete("/_extension/cluster")
    finally:
        handle.close()

    assert response.status_code == 409
    assert (tmp_path / "kube" / "config").exists()          # nothing was half-done


# ---------------------------------------------------------------- credentials stay protected

def test_no_response_or_log_carries_the_token_or_the_record(client, caplog):
    with caplog.at_level(logging.DEBUG):
        responses = [install(client, token=TOKEN_MARKER + ".a.b"), install(client, origin="renew", identity=OTHER_CA),
                     client.get("/_extension/cluster"), client.delete("/_extension/cluster")]

    for response in responses:
        assert TOKEN_MARKER not in response.text
    assert TOKEN_MARKER not in caplog.text
