"""Watching a failure the developer starts: the prediction is fixed first, the observation is only what was read, and
nothing here can stop a node or write to the cluster."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.observation import FailureObservationService, ObservationRefused
from app.main import app
from app.shared.evidence_errors import EvidenceError
from tests.recommendation_evaluation_validation.test_approval_api import override_get_db, sqlite_url  # noqa: F401


class World:
    """A scripted application: three workers, `details` only on worker-1, `reviews` on all three."""

    def __init__(self):
        self.ready = {"worker-1": True, "worker-2": True, "worker-3": True}
        self.replicas = {"details": 1, "reviews": 3}
        self.unreadable = False
        self.uid = "uid-cluster-a"

    def cluster_uid(self):
        return self.uid

    def node_states(self):
        return {name: {"ready": ready, "schedulable": True} for name, ready in self.ready.items()}

    def overview(self, namespace):
        if self.unreadable:
            raise EvidenceError("CLUSTER_UNREADABLE", "no", http_status=503, component="failure-impact")
        nodes = []
        for name in sorted(self.ready):
            down = ["details"] if name == "worker-1" else []
            # The overview does not know node readiness on the discovery path ("ready": None); the service reads it separately.
            nodes.append({"node": name, "ready": None, "servicesDown": down, "servicesDegraded": ["reviews"],
                          "services": [{"service": "details", "readyAfter": 0 if down else 1},
                                       {"service": "reviews", "readyAfter": 2}],
                          "workflowsLost": 1 if down else 0,
                          "workflows": [{"workflowId": "productpage-view", "survives": not down}]})
        return {"scope": "workflow", "namespace": "bookinfo", "nodes": nodes,
                "services": [{"service": s, "readyReplicas": n} for s, n in self.replicas.items()]}


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def tick(self, seconds=5):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def world():
    return World()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def service(sqlite_url, world, clock):  # noqa: F811
    return FailureObservationService(get_engine(sqlite_url), world.overview, world.node_states, world.cluster_uid, clock=clock)


def test_the_prediction_is_fixed_before_anything_fails(service, world):
    record = service.start("bookinfo", "asesh")

    assert record["status"] == "WATCHING" and record["injectedByThisApp"] is False
    assert record["predicted"]["worker-1"]["servicesDown"] == ["details"]
    assert record["comparison"]["state"] == "NO_FAILURE_SEEN"
    world.ready["worker-1"] = False
    world.replicas["details"] = 0                       # changing the world later cannot rewrite the prediction
    assert service.read(record["observationId"])["predicted"]["worker-1"]["servicesDown"] == ["details"]


def test_a_failure_the_developer_causes_is_observed_and_compared_with_the_prediction(service, world, clock):
    started = service.start("bookinfo", None)
    world.ready["worker-1"] = False
    world.replicas.update(details=0, reviews=2)
    clock.tick()
    during = service.read(started["observationId"])

    assert during["status"] == "NODE_DOWN" and during["failedNode"] == "worker-1"
    assert during["comparison"]["state"] == "PROVISIONAL"

    world.ready["worker-1"] = True
    world.replicas.update(details=1, reviews=3)
    clock.tick()
    done = service.read(started["observationId"])

    assert done["status"] == "RECOVERED" and done["active"] is False
    comparison = done["comparison"]
    assert comparison["state"] == "MATCHED"
    assert comparison["observedServicesDown"] == ["details"] == comparison["predictedServicesDown"]
    assert comparison["observedServicesDegraded"] == ["reviews"]
    assert [e["kind"] for e in done["timeline"] if e["kind"] in ("node-not-ready", "node-ready")] == ["node-not-ready", "node-ready"]


def test_a_difference_from_the_prediction_is_reported_not_smoothed_over(service, world, clock):
    started = service.start("bookinfo", None)
    world.ready["worker-1"] = False
    world.replicas.update(details=1, reviews=0)          # details survived, reviews was lost: the opposite of the prediction
    clock.tick()
    service.read(started["observationId"])
    world.ready["worker-1"] = True
    clock.tick()
    comparison = service.read(started["observationId"])["comparison"]

    assert comparison["state"] == "DIFFERENT"
    assert comparison["predictedButStillReady"] == ["details"] and comparison["downButNotPredicted"] == ["reviews"]


def test_workflow_outcomes_are_never_claimed_from_service_readiness(service, world, clock):
    started = service.start("bookinfo", None)
    world.ready["worker-1"] = False
    clock.tick()
    workflows = service.read(started["observationId"])["comparison"]["workflows"]

    assert workflows["observed"] is False and "not observed" in workflows["message"]
    assert "survived" not in {k for k, v in workflows.items() if v is True}


def test_an_unreadable_cluster_is_unknown_never_a_failure_or_a_recovery(service, world, clock):
    started = service.start("bookinfo", None)
    world.unreadable = True
    clock.tick()
    after = service.read(started["observationId"])

    assert after["status"] == "WATCHING" and after["failedNode"] is None
    assert after["reads"]["failed"] == 1 and after["reads"]["lastError"] == "CLUSTER_UNREADABLE"


def test_two_failed_nodes_are_not_compared_with_a_single_node_prediction(service, world, clock):
    started = service.start("bookinfo", None)
    world.ready["worker-1"] = False
    clock.tick()
    service.read(started["observationId"])
    world.ready["worker-2"] = False
    clock.tick()

    assert service.read(started["observationId"])["comparison"]["state"] == "NOT_COMPARABLE"


def test_only_one_watch_at_a_time_and_stopping_frees_the_slot(service):
    first = service.start("bookinfo", None)
    with pytest.raises(ObservationRefused) as refused:
        service.start("bookinfo", None)
    assert refused.value.code == "OBSERVATION_ACTIVE"

    assert service.stop(first["observationId"])["status"] == "STOPPED"
    assert service.start("bookinfo", None)["status"] == "WATCHING"


def test_a_watch_nobody_finishes_expires(service, clock):
    started = service.start("bookinfo", None)
    clock.tick(3 * 3600)

    assert service.read(started["observationId"])["status"] == "EXPIRED"


def test_starting_without_a_readable_cluster_records_nothing(service, world):
    world.unreadable = True
    with pytest.raises(EvidenceError):
        service.start("bookinfo", None)
    assert service.read() is None


def test_a_different_cluster_is_never_read_as_the_one_being_watched(service, world, clock):
    started = service.start("bookinfo", None)
    assert started["cluster"] == {"clusterUid": "uid-cluster-a"} and started["clusterMatch"] == "same"

    world.uid = "uid-cluster-b"                  # another cluster with the same worker and service names
    world.ready["worker-1"] = False
    world.replicas.update(details=0)
    clock.tick()
    other = service.read(started["observationId"])

    assert other["clusterMatch"] == "other" and other["status"] == "WATCHING" and other["failedNode"] is None
    assert other["reads"]["lastError"] == "CLUSTER_CHANGED"

    world.uid = "uid-cluster-a"                  # reconnecting the original cluster resumes the watch
    clock.tick()
    back = service.read(started["observationId"])
    assert back["clusterMatch"] == "same" and back["status"] == "NODE_DOWN" and back["failedNode"] == "worker-1"


def test_a_cluster_that_cannot_be_identified_is_unknown_not_the_same_cluster(service, world, clock):
    started = service.start("bookinfo", None)
    world.uid = None
    world.ready["worker-1"] = False
    clock.tick()
    after = service.read(started["observationId"])

    assert after["clusterMatch"] == "unknown" and after["failedNode"] is None
    assert after["reads"]["lastError"] == "CLUSTER_IDENTITY_UNKNOWN"


def test_a_watch_cannot_start_when_the_cluster_cannot_be_identified(service, world):
    world.uid = None
    with pytest.raises(ObservationRefused) as refused:
        service.start("bookinfo", None)
    assert refused.value.code == "CLUSTER_IDENTITY_UNKNOWN"
    assert service.read() is None


def test_the_record_survives_a_new_service_instance(sqlite_url, world, clock):  # noqa: F811
    first = FailureObservationService(get_engine(sqlite_url), world.overview, world.node_states, world.cluster_uid, clock=clock).start("bookinfo", None)
    again = FailureObservationService(get_engine(sqlite_url), world.overview, world.node_states, world.cluster_uid, clock=clock).read()

    assert again["observationId"] == first["observationId"] and again["predicted"] == first["predicted"]


def test_routes_start_read_and_stop_a_watch(override_get_db, monkeypatch):  # noqa: F811
    world = World()
    monkeypatch.setattr("app.api.recommendations.build_cluster_failure_overview", world.overview)
    monkeypatch.setattr("app.api.recommendations.read_node_states", world.node_states)
    monkeypatch.setattr("app.api.recommendations.read_cluster_uid", world.cluster_uid)
    client = TestClient(app)

    started = client.post("/api/recommendations/failure-observations", json={"namespace": "bookinfo"},
                          headers={"X-Operator-Name": "asesh"})
    assert started.status_code == 201 and started.json()["startedBy"] == "asesh"
    assert client.post("/api/recommendations/failure-observations", json={}).status_code == 409
    current = client.get("/api/recommendations/failure-observations/current").json()["observation"]
    assert current["observationId"] == started.json()["observationId"]
    stopped = client.post(f"/api/recommendations/failure-observations/{current['observationId']}/stop")
    assert stopped.json()["status"] == "STOPPED"
    assert client.get("/api/recommendations/failure-observations").json()["observations"][0]["status"] == "STOPPED"
