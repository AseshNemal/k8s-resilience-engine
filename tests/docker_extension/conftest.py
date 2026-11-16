"""Shared set-up for the extension's backend tests.

The connection tests are about the credential, not about applied changes, and they run without a migrated database. Disconnect
now reads the applied-change ledger first, so here that ledger is an empty one unless a test supplies its own
(``test_disconnect_outstanding.py`` does, against a real migrated database).
"""

import pytest

from extension import cluster_connection as cc


class _Closed:
    def close(self) -> None:
        pass


class EmptyLedger:
    def outstanding(self, *, advance_resets: bool = False) -> dict:
        return {"connectedClusterUid": None, "changes": [], "blocking": [], "resetInProgress": False}

    def note_left_at_disconnect(self, application_ids) -> None:
        raise AssertionError("nothing is outstanding, so nothing can be left behind")


@pytest.fixture(autouse=True)
def empty_change_ledger(monkeypatch):
    monkeypatch.setattr(cc, "_change_ledger", lambda: (_Closed(), EmptyLedger()))
