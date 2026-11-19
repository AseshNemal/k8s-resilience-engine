"""The cluster-status read every page asks before it presents anything as live: one word for what the cluster is doing now."""

import pytest
from fastapi.testclient import TestClient

from app.api import recommendations
from app.integrations.kubernetes.client import AuthFailedError, ConnectionFailedError, NotConnectedError
from app.main import app


class Gateway:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def __call__(self, *_args, **_kwargs):
        return self

    def identity(self):
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


class Identity:
    cluster_uid, context, api_server, node_names = "uid-1", "kind-x", "https://127.0.0.1:6443", ("a", "b", "c")


@pytest.mark.parametrize("behaviour, state, uid", [
    (Identity(), "connected", "uid-1"),
    (NotConnectedError("no credential"), "not-connected", None),
    (ConnectionFailedError("refused"), "unreachable", None),
    (AuthFailedError("rejected"), "access-denied", None),
])
def test_the_status_is_the_clusters_own_answer_right_now(monkeypatch, behaviour, state, uid):
    monkeypatch.setattr(recommendations, "KubernetesTestClusterGateway", Gateway(behaviour))

    body = TestClient(app).get("/api/recommendations/cluster-status").json()

    assert body["state"] == state and body["clusterUid"] == uid and body["checkedAt"]
    assert (body["nodeCount"] == 3) is (state == "connected")


def test_a_cluster_that_answers_without_an_identity_is_not_called_connected(monkeypatch):
    class Anonymous(Identity):
        cluster_uid = None
    monkeypatch.setattr(recommendations, "KubernetesTestClusterGateway", Gateway(Anonymous()))

    assert TestClient(app).get("/api/recommendations/cluster-status").json()["state"] == "unidentified"
