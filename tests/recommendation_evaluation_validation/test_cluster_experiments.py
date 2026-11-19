"""Experiments of the connected cluster: only its own records are counted, accuracy is a count of matches, nothing is invented."""

from fastapi.testclient import TestClient

from app.api import recommendations
from app.engine.recommendation_evaluation_validation import cluster_experiments as ce
from app.main import app

LIVE = {"state": "connected", "clusterUid": "uid-1", "context": "docker-desktop"}


def run(key="run-1", uid="uid-1", name="research"):
    return {"runKey": key, "clusterUid": uid, "targetName": name, "namespace": "bookinfo", "startedAt": "2026-11-18T10:00:00+00:00", "finishedAt": "2026-11-18T10:20:00+00:00"}


def experiment(worker, *, kind="candidate", status="PASS", down=(), predicted_down=(), before=(), ok=True, timeline=False):
    services = {"productpage": {"ok": "productpage" not in down}, "ratings": {"ok": "ratings" not in down}, "reviews": {"ok": "reviews" not in down}}
    outcome = {"readiness": {"services": services, "holds": ok},
               "predicted": {"servicesDown": list(predicted_down), "servicesDownBefore": list(before),
                             "workflowServices": ["productpage", "ratings", "reviews"], "workflowSurvives": not predicted_down},
               "recovery": {"verified": True}, "probe": {}, "timeline": {"samples": [{}]} if timeline else None}
    return {"kind": kind, "candidateId": "c1" if kind == "candidate" else None, "worker": worker, "repeat": 1, "status": status, "outcome": outcome,
            "durationS": 70.0, "startedAt": "2026-11-18T10:01:00+00:00", "finishedAt": "2026-11-18T10:03:00+00:00"}


def test_only_the_connected_clusters_experiments_are_figures_and_others_are_counted_apart():
    mine = (run(), [experiment("w1")])
    other = (run("run-2", uid="uid-9", name="kind-other"), [experiment("w1", down=("ratings",))])

    out = ce.build(LIVE, [mine, other], [])

    assert [r["runKey"] for r in out["experiments"]] == ["run-1"]
    assert out["elsewhere"] == {"count": 1, "clusters": ["uid-9"]}
    assert out["accuracy"]["experimentsCompared"] == 1            # the other cluster's mismatch is not in the figures


def test_accuracy_counts_matches_and_names_the_differences():
    runs = [(run(), [experiment("w1", down=("ratings",), predicted_down=("ratings",)), experiment("w2", down=("reviews",), predicted_down=()),
                     experiment("w3")])]

    accuracy = ce.build(LIVE, runs, [])["accuracy"]

    assert (accuracy["experimentsCompared"], accuracy["matched"]) == (3, 2)
    assert accuracy["agreement"] == 0.6667
    assert [d["failedNode"] for d in accuracy["differences"]] == ["w2"]
    assert {n["node"]: n["matched"] for n in accuracy["byNode"]} == {"w1": 1, "w2": 0, "w3": 1}


def test_with_nothing_compared_accuracy_is_unknown_not_a_hundred_percent():
    out = ce.build(LIVE, [], [])

    assert out["experiments"] == [] and out["accuracy"]["agreement"] is None and out["accuracy"]["experimentsCompared"] == 0


def test_a_disconnected_cluster_has_no_figures_at_all_only_what_is_saved_elsewhere():
    out = ce.build({"state": "not-connected", "clusterUid": None}, [(run(), [experiment("w1")])], [])

    assert out["connected"] is False and out["experiments"] == [] and out["accuracy"] is None
    assert out["elsewhere"]["count"] == 1


def test_experiments_that_never_ran_are_not_experiments():
    out = ce.build(LIVE, [(run(), [experiment("w1", status="PASS"), {**experiment("w2"), "status": "SKIPPED"}, {**experiment("w3"), "status": "PENDING"}])], [])

    assert [r["failedNode"] for r in out["experiments"]] == ["w1"]


def test_the_before_row_is_compared_with_what_the_model_said_before_the_change():
    base = experiment("w1", kind="baseline", down=("ratings",))
    change = experiment("w1", before=("ratings",), predicted_down=())

    out = ce.build(LIVE, [(run(), [base, change])], [])

    row = next(r for r in out["experiments"] if not r["withChange"])
    assert row["predictedDown"] == ["ratings"] and row["observedDown"] == ["ratings"] and row["match"] is True


def test_a_record_that_stored_no_cluster_is_never_attributed_to_the_connected_one():
    out = ce.build(LIVE, [(run(uid=None), [experiment("w1")])], [])

    assert out["experiments"] == [] and out["unattributed"]["count"] == 1 and out["accuracy"]["experimentsCompared"] == 0


def watch(failed="w2", uid="uid-1", multiple=False, least=None):
    return {"observationId": "obs-1", "status": "RECOVERED", "namespace": "bookinfo", "failedNode": failed, "multipleFailures": multiple,
            "cluster": {"clusterUid": uid}, "startedAt": "2026-11-18T09:00:00+00:00", "endedAt": "2026-11-18T09:10:00+00:00",
            "baseline": {"services": {"details": 1, "ratings": 1, "reviews": 3}}, "observed": {"leastReady": least or {"details": 0, "ratings": 0, "reviews": 3}},
            "predicted": {"w2": {"servicesDown": ["details", "ratings"]}}}


def test_a_failure_the_developer_caused_and_this_app_watched_is_compared_the_same_way():
    out = ce.build(LIVE, [], [watch()])

    row = out["experiments"][0]
    assert row["source"] == "watched-failure" and row["observedDown"] == ["details", "ratings"] and row["match"] is True


def test_a_watch_with_two_failed_nodes_is_listed_but_not_compared():
    out = ce.build(LIVE, [], [watch(multiple=True)])

    assert out["experiments"][0]["match"] is None and out["accuracy"]["experimentsCompared"] == 0


def test_a_watch_where_nothing_went_down_is_not_an_experiment():
    assert ce.build(LIVE, [], [watch(failed=None)])["experiments"] == []


# --- the endpoint ----------------------------------------------------------------------------------------------------------

class Repository:
    def history(self, _limit):
        return [run()]

    def experiments(self, _key):
        return [experiment("w1", down=("ratings",), predicted_down=("ratings",))]


class Observations:
    def history(self, _limit):
        return []


def client(monkeypatch, status):
    monkeypatch.setattr(recommendations, "read_cluster_status", lambda: status)
    app.dependency_overrides[recommendations.get_loop_runtime] = lambda: (Repository(), None)
    app.dependency_overrides[recommendations.get_failure_observation_service] = lambda: Observations()
    return TestClient(app)


def test_the_endpoint_reads_the_cluster_identity_now_and_shows_nothing_when_it_is_out_of_reach(monkeypatch):
    try:
        body = client(monkeypatch, {"state": "unreachable", "clusterUid": None}).get("/api/recommendations/cluster-experiments").json()
    finally:
        app.dependency_overrides.clear()

    assert body["connected"] is False and body["experiments"] == [] and body["accuracy"] is None and body["elsewhere"]["count"] == 1


def test_the_endpoint_returns_the_connected_clusters_experiments(monkeypatch):
    try:
        body = client(monkeypatch, LIVE).get("/api/recommendations/cluster-experiments").json()
    finally:
        app.dependency_overrides.clear()

    assert body["connected"] is True and len(body["experiments"]) == 1 and body["accuracy"]["matched"] == 1
