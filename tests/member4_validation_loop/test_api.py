"""The validation-loop routes: plan, approve once, watch, abort, recover, history, proof; and the Apply/Reset guard."""

import pytest
from fastapi.testclient import TestClient

from app.api.validation_loop import get_application_active, get_environment_factory, get_loop_runtime
from app.engine.recommendation_evaluation_validation.validation_loop.environment import DISPOSABLE, DOCKER_DESKTOP
from app.engine.recommendation_evaluation_validation.validation_loop.models import RunStatus
from app.engine.recommendation_evaluation_validation.validation_loop.worker import LoopWorker
from app.main import app
from tests.support.validation_loop_fake_environment import NAMESPACE, FakeEnvironment, readiness

BASE = "/api/recommendations/validation-loop"
PLAN = {"targetKind": DISPOSABLE, "mode": "QUICK", "workflowId": "productpage-view", "namespace": NAMESPACE}
PROBE = {"url": "http://127.0.0.1:18080/productpage", "expectedStatus": 200, "mustContain": ["Book Details"],
         "mustNotContain": ["currently unavailable"]}


@pytest.fixture
def worker(repo):
    return LoopWorker(repo)


@pytest.fixture
def env():
    return FakeEnvironment()


@pytest.fixture
def client(repo, worker, env, monkeypatch):
    monkeypatch.setenv("RESILIENCE_ALLOW_CLUSTER_WRITES", "1")           # what the extension's compose file does
    app.dependency_overrides[get_loop_runtime] = lambda: (repo, worker)
    app.dependency_overrides[get_environment_factory] = lambda: (lambda kind, source="FROZEN": env)
    app.dependency_overrides[get_application_active] = lambda: (lambda: False)
    yield TestClient(app)
    for dependency in (get_loop_runtime, get_environment_factory, get_application_active):
        app.dependency_overrides.pop(dependency, None)


def start(client, **changes):
    plan = client.post(f"{BASE}/plan", json={**PLAN, **changes}).json()
    return client.post(f"{BASE}/runs", json={"plan": {**PLAN, **changes}, "planDigest": plan["planDigest"]})


def test_the_plan_route_returns_what_a_run_would_do(client):
    body = client.post(f"{BASE}/plan", json=PLAN).json()

    assert body["canApprove"] is True and body["mode"]["mode"] == "QUICK" and len(body["candidates"]) == 1
    assert body["planDigest"] and body["target"]["kind"] == DISPOSABLE and body["acknowledgementsNeeded"] == []


def test_an_unknown_mode_is_a_422_with_a_code(client):
    answer = client.post(f"{BASE}/plan", json={**PLAN, "mode": "turbo"})

    assert answer.status_code == 422 and answer.json()["detail"]["code"] == "UNKNOWN_MODE"


def test_one_post_approves_and_starts_the_run_and_the_current_route_shows_persisted_progress(client, worker):
    started = start(client)
    worker.join(15)

    assert started.status_code == 200 and started.json()["status"] in ("RUNNING", "COMPLETED")
    run = client.get(f"{BASE}/current").json()["run"]
    assert run["status"] == "COMPLETED" and run["result"]["winner"] and run["metrics"]["finished"] == 3
    assert "research" not in run and "research" in client.get(f"{BASE}/current", params={"research": "true"}).json()["run"]


def test_an_approval_for_a_digest_that_no_longer_matches_is_a_409_and_starts_nothing(client, repo):
    answer = client.post(f"{BASE}/runs", json={"plan": PLAN, "planDigest": "0" * 64})

    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "PLAN_CHANGED" and repo.latest_run() is None


def test_a_workload_that_needs_acknowledging_is_a_409_until_it_is(client, env):
    env.extra_pods = [{"namespace": "other", "name": "db-1", "node": "w1", "ready": True, "ownerKind": "ReplicaSet", "ownerName": "db-abc12"},
                      {"namespace": "kube-system", "name": "coredns-1", "node": "w1", "ready": True, "ownerKind": "ReplicaSet", "ownerName": "coredns-abc12"},
                      {"namespace": "kube-system", "name": "coredns-2", "node": "w2", "ready": True, "ownerKind": "ReplicaSet", "ownerName": "coredns-abc12"}]
    plan = client.post(f"{BASE}/plan", json=PLAN).json()
    assert plan["acknowledgementsNeeded"] == ["other/Deployment/db"]

    refused = client.post(f"{BASE}/runs", json={"plan": PLAN, "planDigest": plan["planDigest"]})
    accepted = client.post(f"{BASE}/runs", json={"plan": PLAN, "planDigest": plan["planDigest"],
                                                 "acknowledgedWorkloads": ["other/Deployment/db"]})

    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "ACKNOWLEDGEMENT_MISSING" and accepted.status_code == 200


def test_the_docker_desktop_acknowledgement_is_required_to_start(client):
    env = FakeEnvironment(kind=DOCKER_DESKTOP, name="desktop", readiness=readiness())
    app.dependency_overrides[get_environment_factory] = lambda: (lambda kind, source="FROZEN": env)
    body = {**PLAN, "targetKind": DOCKER_DESKTOP}
    plan = client.post(f"{BASE}/plan", json=body).json()

    refused = client.post(f"{BASE}/runs", json={"plan": body, "planDigest": plan["planDigest"]})
    accepted = client.post(f"{BASE}/runs", json={"plan": body, "planDigest": plan["planDigest"], "desktopAcknowledged": True})

    assert refused.json()["detail"]["code"] == "DESKTOP_ACKNOWLEDGEMENT_REQUIRED" and accepted.status_code == 200


def test_the_probe_test_route_reports_the_samples(client):
    answer = client.post(f"{BASE}/probe-test", json={"targetKind": DISPOSABLE, "probe": PROBE})

    assert answer.status_code == 200 and answer.json()["survives"] is True and answer.json()["samples"] == 3


def test_history_proof_and_a_single_run_are_read_from_the_database(client, worker):
    started = start(client).json()
    worker.join(15)
    plan = client.post(f"{BASE}/plan", json=PLAN).json()
    sha = plan["candidates"][0]["yamlSha256"] if plan["candidates"] else None

    history = client.get(f"{BASE}/history").json()["runs"]
    one = client.get(f"{BASE}/runs/{started['runKey']}").json()["run"]
    proof = client.get(f"{BASE}/proof", params={"namespace": NAMESPACE, "workflowId": "productpage-view", "candidateSha256": sha}).json()

    assert history[0]["runKey"] == started["runKey"] and history[0]["winner"] and one["status"] == "COMPLETED"
    assert proof["state"] == "PROVEN" and proof["target"]["kind"] == DISPOSABLE


def test_an_unknown_run_is_a_404(client):
    for method, path in (("get", "/runs/nope"), ("post", "/runs/nope/abort"), ("post", "/runs/nope/recover")):
        assert getattr(client, method)(BASE + path).status_code == 404


def test_abort_and_recover_of_a_run_in_the_wrong_state_are_409(client, worker):
    started = start(client).json()
    worker.join(15)

    for path in ("abort", "recover"):
        answer = client.post(f"{BASE}/runs/{started['runKey']}/{path}")
        assert answer.status_code == 409 and answer.json()["detail"]["code"] in ("RUN_NOT_RUNNING", "RUN_NOT_RECOVERABLE")


def test_a_run_that_needs_a_person_is_recovered_by_the_recover_route_and_the_next_run_is_then_allowed(client, repo, worker):
    started = start(client).json()
    worker.join(15)
    repo.set_status(started["runKey"], RunStatus.NEEDS_MANUAL_RESTORE)
    assert client.post(f"{BASE}/plan", json=PLAN).json()["canApprove"] is False

    recovered = client.post(f"{BASE}/runs/{started['runKey']}/recover").json()["run"]

    assert recovered["status"] == "INTERRUPTED" and client.post(f"{BASE}/plan", json=PLAN).json()["canApprove"] is True


def test_targets_lists_both_clusters_with_what_each_one_would_do_to_the_person(client):
    targets = client.get(f"{BASE}/targets").json()

    assert {t["kind"] for t in targets["targets"]} == {DOCKER_DESKTOP, DISPOSABLE}
    desktop = next(t for t in targets["targets"] if t["kind"] == DOCKER_DESKTOP)
    assert "every day" in desktop["warning"] and targets["blockingRun"] is None


def test_an_unreachable_target_is_a_state_with_a_reason_not_a_server_error(client):
    class Down:
        def identity(self):
            raise RuntimeError("the cluster is not reachable")

    app.dependency_overrides[get_environment_factory] = lambda: (lambda kind, source="FROZEN": Down())

    targets = client.get(f"{BASE}/targets")

    assert targets.status_code == 200 and all(t["available"] is False and "not reachable" in t["reason"] for t in targets.json()["targets"])


# --- Apply and Reset must not run while a validation run uses the cluster --------------------------------------------------------

def test_apply_and_reset_are_refused_while_a_docker_desktop_run_is_active(client, repo, worker):
    env = FakeEnvironment(kind=DOCKER_DESKTOP, name="desktop", readiness=readiness())
    app.dependency_overrides[get_environment_factory] = lambda: (lambda kind, source="FROZEN": env)
    body = {**PLAN, "targetKind": DOCKER_DESKTOP}
    plan = client.post(f"{BASE}/plan", json=body).json()
    started = client.post(f"{BASE}/runs", json={"plan": body, "planDigest": plan["planDigest"], "desktopAcknowledged": True}).json()
    worker.join(15)
    repo.set_status(started["runKey"], RunStatus.NEEDS_MANUAL_RESTORE)

    for path, payload in (("apply", {"appliedBy": "x"}), ("reset", {"resetBy": "x"})):
        for prefix in ("/api/recommendations", "/api/recommendations/declared"):
            answer = client.post(f"{prefix}/some-id/{path}", json=payload)
            assert answer.status_code == 409 and answer.json()["detail"]["code"] == "VALIDATION_RUN_ACTIVE"


def test_apply_is_not_blocked_by_a_run_on_the_disposable_cluster(client, repo, worker):
    started = start(client).json()
    worker.join(15)
    repo.set_status(started["runKey"], RunStatus.NEEDS_MANUAL_RESTORE)

    answer = client.post("/api/recommendations/some-id/apply", json={"appliedBy": "x"})

    assert answer.json().get("detail", {}).get("code") != "VALIDATION_RUN_ACTIVE"


# --- the deployment must have armed writes --------------------------------------------------------------------------------------

def test_a_run_and_a_recovery_do_not_start_unless_the_deployment_armed_writes(client, monkeypatch):
    plan = client.post(f"{BASE}/plan", json=PLAN).json()
    monkeypatch.delenv("RESILIENCE_ALLOW_CLUSTER_WRITES")

    run = client.post(f"{BASE}/runs", json={"plan": PLAN, "planDigest": plan["planDigest"]})
    recover = client.post(f"{BASE}/runs/anything/recover")

    assert run.status_code == recover.status_code == 403 and run.json()["detail"]["code"] == "CLUSTER_WRITES_NOT_ENABLED"
    assert client.post(f"{BASE}/plan", json=PLAN).status_code == 200            # planning and reading still work


def test_the_in_flight_rule_covers_applying_and_an_unfinished_rollout(monkeypatch):
    from types import SimpleNamespace

    from app.api import validation_loop as module
    from app.engine.recommendation_evaluation_validation.apply_test.repository import RunRepository
    monkeypatch.setattr(RunRepository, "active", lambda _: None)

    def active_for(change):
        class Repo:
            def __init__(self, db): ...
            def latest(self): return change

        class Session:
            def __init__(self, engine): ...
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(module, "AppliedChangeRepository", Repo)
        monkeypatch.setattr(module, "Session", Session)
        monkeypatch.setattr(module, "get_engine", lambda url: None)
        return module.get_application_active()()

    assert active_for(None) is False
    assert active_for(SimpleNamespace(status="APPLYING", rollout=None)) is True
    assert active_for(SimpleNamespace(status="RESET", rollout={"state": "rolling-out"})) is True
    assert active_for(SimpleNamespace(status="APPLIED", rollout={"state": "completed"})) is False
