"""Disconnect never removes a credential that a change still needs, and never does so without a decision.

The ledger is a stand-in here (what ``ApplicationService.outstanding`` returns is covered, against a real database, in
``tests/recommendation_evaluation_validation/test_reset_reliability.py``); these tests are about what Disconnect does with it.
"""

import pytest
from fastapi.testclient import TestClient

from extension import cluster_connection as cc
from extension.asgi import app
from tests.docker_extension.test_cluster_connection import kubeconfig, put


class Ledger:
    def __init__(self, *, blocking=(), reset_in_progress=False, fails=False):
        self.rows = [{"applicationId": f"apply-{i}", "recommendationId": name, "tier": "research", "status": "APPLIED",
                      "namespace": "shop", "deployments": ["db"], "clusterMatch": "same"} for i, name in enumerate(blocking)]
        self.reset_in_progress, self.fails = reset_in_progress, fails
        self.left: list[str] = []
        self.closed = False

    def close(self):
        self.closed = True

    def outstanding(self, *, advance_resets=False):
        if self.fails:
            raise RuntimeError("database is locked")
        assert advance_resets, "a reset nobody looked at must be read before it is called in progress"
        return {"connectedClusterUid": "uid", "changes": self.rows, "blocking": self.rows,
                "resetInProgress": self.reset_in_progress}

    def note_left_at_disconnect(self, application_ids):
        self.left.extend(application_ids)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "KUBECONFIG_PATH", tmp_path / "kube" / "config")
    monkeypatch.setattr(cc, "_verify_access", lambda document: None)
    return TestClient(app)


def use(monkeypatch, ledger):
    monkeypatch.setattr(cc, "_change_ledger", lambda: (ledger, ledger))
    return ledger


def connected(tmp_path):
    return (tmp_path / "kube" / "config").exists()


def test_disconnect_with_a_change_still_applied_is_refused_and_names_it(client, tmp_path, monkeypatch):
    use(monkeypatch, Ledger(blocking=["rec-db-spread"]))
    put(client, kubeconfig())

    response = client.delete("/_extension/cluster")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "OUTSTANDING_CHANGES"
    assert [row["recommendationId"] for row in detail["changes"]] == ["rec-db-spread"]
    assert connected(tmp_path), "the credential a reset needs is still there"


def test_keeping_the_changes_is_an_explicit_choice_and_is_written_on_each(client, tmp_path, monkeypatch):
    ledger = use(monkeypatch, Ledger(blocking=["rec-db-spread", "rec-web-replicas"]))
    put(client, kubeconfig())

    response = client.delete("/_extension/cluster", params={"outstanding": "keep"})

    assert response.status_code == 200 and response.json()["connected"] is False
    assert not connected(tmp_path)
    assert ledger.left == ["apply-0", "apply-1"] and ledger.closed


def test_a_reset_in_progress_is_never_abandoned_even_when_asked_to_keep(client, tmp_path, monkeypatch):
    use(monkeypatch, Ledger(blocking=["rec-db-spread"], reset_in_progress=True))
    put(client, kubeconfig())

    for params in ({}, {"outstanding": "keep"}):
        response = client.delete("/_extension/cluster", params=params)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "RESET_IN_PROGRESS"
    assert connected(tmp_path)


def test_a_ledger_that_cannot_be_read_stops_the_disconnect_instead_of_assuming_nothing_is_applied(client, tmp_path, monkeypatch):
    ledger = use(monkeypatch, Ledger(fails=True))
    put(client, kubeconfig())

    response = client.delete("/_extension/cluster")

    assert response.status_code == 503 and response.json()["detail"]["code"] == "CHANGE_LEDGER_UNAVAILABLE"
    assert connected(tmp_path) and ledger.closed


def test_with_nothing_outstanding_disconnect_is_what_it_always_was(client, tmp_path, monkeypatch):
    ledger = use(monkeypatch, Ledger())
    put(client, kubeconfig())

    assert client.delete("/_extension/cluster").json() == {"connected": False}
    assert not connected(tmp_path) and ledger.left == []


def test_when_no_cluster_is_connected_the_ledger_is_not_even_read(client, monkeypatch):
    use(monkeypatch, Ledger(fails=True))

    assert client.delete("/_extension/cluster").json() == {"connected": False}


def test_only_keep_is_accepted_as_the_choice(client, monkeypatch):
    use(monkeypatch, Ledger(blocking=["rec-db-spread"]))
    put(client, kubeconfig())

    assert client.delete("/_extension/cluster", params={"outstanding": "discard"}).status_code == 422


def test_the_page_can_ask_what_is_outstanding_without_disconnecting(client, tmp_path, monkeypatch):
    use(monkeypatch, Ledger(blocking=["rec-db-spread"]))
    put(client, kubeconfig())

    body = client.get("/_extension/cluster/outstanding").json()

    assert [row["recommendationId"] for row in body["blocking"]] == ["rec-db-spread"] and connected(tmp_path)


def test_the_helpers_disconnect_records_what_was_left_instead_of_leaving_a_dead_credential(tmp_path, monkeypatch, capsys):
    from extension import cluster_ingest

    monkeypatch.setattr(cc, "KUBECONFIG_PATH", tmp_path / "kube" / "config")
    monkeypatch.setattr(cc, "_verify_access", lambda document: None)
    ledger = use(monkeypatch, Ledger(blocking=["rec-db-spread"]))
    put(TestClient(app), kubeconfig())

    assert cluster_ingest.run(["--disconnect"], None) == 0

    assert not connected(tmp_path) and ledger.left == ["apply-0"]
