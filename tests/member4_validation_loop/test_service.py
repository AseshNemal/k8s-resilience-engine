"""The service end to end over the fake environment, a real database and the real background worker."""

import pytest

from app.engine.recommendation_evaluation_validation.validation import ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation_loop.environment import DISPOSABLE, DOCKER_DESKTOP, ProbeDefinition
from app.engine.recommendation_evaluation_validation.validation_loop.models import Mode, RunStatus
from app.engine.recommendation_evaluation_validation.validation_loop.service import (
    ApprovalInput,
    PlanRequest,
    ValidationLoopService,
)
from app.engine.recommendation_evaluation_validation.validation_loop.worker import LoopWorker
from tests.member4_validation_loop.conftest import NOW
from tests.support.validation_loop_fake_environment import NAMESPACE, FakeEnvironment, readiness

PROBE = ProbeDefinition("http://127.0.0.1:18080/productpage", 200, ("Book Details",), ("currently unavailable",))


def service(repo, env=None, **options):
    env = env or FakeEnvironment()
    return ValidationLoopService(env, repo, LoopWorker(repo), clock=lambda: NOW, **options), env


def request(mode=Mode.QUICK, **options):
    return PlanRequest(mode=mode, workflow_id="productpage-view", namespace=NAMESPACE, **options)


def approve(svc, req, plan=None, **approval):
    plan = plan or svc.plan(req)
    return svc.approve(req, ApprovalInput(plan.digest, **approval))


def finish(svc, run_key):
    svc._worker.join(15)
    return svc.view(run_key, research=True)


# --- planning ------------------------------------------------------------------------------------------------------

def test_a_plan_says_what_will_happen_in_terms_the_person_can_check(repo):
    svc, _ = service(repo)

    plan = svc.plan(request())

    assert plan.can_approve and plan.mode_plan.workers == ("w1", "w2", "w3") and len(plan.candidates) == 1
    data = plan.to_dict()
    assert data["mode"]["plannedFailures"] == 3 and data["target"]["kind"] == DISPOSABLE and data["riskiestWorker"] == "w1"
    assert set(data["candidates"][0]["predictedPerWorker"]) == {"w1", "w2", "w3"}
    assert any("preliminary" in note for note in data["notes"]) and any("L1" in note for note in data["notes"])


def test_the_plan_digest_is_stable_for_the_same_cluster_and_changes_with_what_is_approved(repo):
    svc, _ = service(repo)

    assert svc.plan(request()).digest == svc.plan(request()).digest
    assert svc.plan(request()).digest != svc.plan(request(Mode.SMOKE)).digest
    assert svc.plan(request()).digest != svc.plan(request(probe=PROBE)).digest


def test_full_plans_two_candidates_where_they_exist_and_always_a_measured_baseline(repo):
    svc, _ = service(repo)

    plan = svc.plan(request(Mode.FULL))

    assert plan.mode_plan.measure_baseline is True and plan.mode_plan.repeats == 2
    assert plan.mode_plan.planned_failures(len(plan.candidates)) == len(plan.candidates) * 3 * 2 + 3


def test_a_blocked_or_unreadable_cluster_cannot_be_approved(repo):
    svc, _ = service(repo, FakeEnvironment(uid=None))

    plan = svc.plan(request())

    assert not plan.can_approve and "CLUSTER_IDENTITY_UNVERIFIED" in {i.code for i in plan.blocking}


def test_an_essential_workload_that_would_go_down_blocks_the_plan(repo):
    pods = [{"namespace": "kube-system", "name": "coredns-1", "node": "w1", "ready": True, "ownerKind": "ReplicaSet", "ownerName": "coredns-abc12"}]
    svc, _ = service(repo, FakeEnvironment(extra_pods=pods))

    plan = svc.plan(request())

    assert not plan.can_approve and "ESSENTIAL_WORKLOAD" in {i.code for i in plan.blocking}
    assert "coredns" in " ".join(i.message for i in plan.blocking)


def test_unreadable_pods_make_the_plan_fail_closed(repo):
    env = FakeEnvironment()

    def broken():
        raise RuntimeError("API unreachable")

    env.read_pods = broken
    plan = service(repo, env)[0].plan(request())

    assert not plan.can_approve and "EVIDENCE_INCOMPLETE" in {i.code for i in plan.blocking}


def test_docker_desktop_limits_an_unverified_restart_window_to_the_smoke_test(repo):
    env = FakeEnvironment(kind=DOCKER_DESKTOP, name="desktop", readiness=readiness(window="UNVERIFIED"))
    svc, _ = service(repo, env)

    quick, smoke = svc.plan(request(Mode.QUICK)), svc.plan(request(Mode.SMOKE))

    assert "SMOKE_TEST_FIRST" in {i.code for i in quick.blocking} and smoke.can_approve


def test_a_valid_window_observed_before_lifts_the_smoke_only_limit(repo):
    env = FakeEnvironment(kind=DOCKER_DESKTOP, name="desktop", readiness=readiness(window="OK"))

    assert service(repo, env)[0].plan(request(Mode.QUICK)).can_approve


def test_workers_that_cannot_be_identified_or_controlled_stop_the_plan(repo):
    env = FakeEnvironment(kind=DOCKER_DESKTOP, name="desktop")
    bad = readiness()
    object.__setattr__(bad.workers[0], "identifiable", False)
    env.readiness = bad

    assert "WORKERS_NOT_CONTROLLABLE" in {i.code for i in service(repo, env)[0].plan(request()).blocking}


def test_a_running_application_change_blocks_a_docker_desktop_plan(repo):
    env = FakeEnvironment(kind=DOCKER_DESKTOP, name="desktop", readiness=readiness())
    svc, _ = service(repo, env, application_active=lambda: True)

    assert "APPLICATION_ACTIVE" in {i.code for i in svc.plan(request()).blocking}


# --- approving: one approval, exactly the plan reviewed -------------------------------------------------------------

def test_one_approval_runs_the_whole_plan_and_the_page_reads_persisted_state(repo):
    svc, env = service(repo)

    started = approve(svc, request())
    view = finish(svc, started["runKey"])

    assert view["status"] == "COMPLETED" and view["result"]["winner"] is not None and view["mode"] == "QUICK"
    assert view["preliminary"] is True and view["metrics"]["finished"] == 3 and view["metrics"]["killsUsed"] == 3
    assert [c["status"] for c in view["matrix"]["rows"][0]["cells"]] == ["PASS", "PASS", "PASS"]
    assert {action for _, action in env.armed} == {"STOP_KIND_WORKER_CONTAINER"} and len(env.armed) == 3


def test_an_approval_for_a_plan_that_changed_is_refused_before_anything_starts(repo):
    svc, env = service(repo)
    plan = svc.plan(request())
    env.cluster.graph.nodes[0].name = "w1-renamed"            # the cluster moved after the person looked

    with pytest.raises(ValidationRefusedError) as refused:
        svc.approve(request(), ApprovalInput(plan.digest))
    assert refused.value.code == "PLAN_CHANGED" and repo.latest_run() is None


def test_an_approval_for_a_blocked_plan_is_refused(repo):
    svc, _ = service(repo, FakeEnvironment(uid=None))
    plan = svc.plan(request())

    with pytest.raises(ValidationRefusedError) as refused:
        svc.approve(request(), ApprovalInput(plan.digest))
    assert refused.value.code == "PLAN_BLOCKED"


def test_a_workload_that_would_go_down_must_be_acknowledged_by_name(repo):
    pods = [{"namespace": "other", "name": "db-1", "node": "w1", "ready": True, "ownerKind": "ReplicaSet", "ownerName": "db-abc12"},
            {"namespace": "kube-system", "name": "coredns-1", "node": "w1", "ready": True, "ownerKind": "ReplicaSet", "ownerName": "coredns-abc12"},
            {"namespace": "kube-system", "name": "coredns-2", "node": "w2", "ready": True, "ownerKind": "ReplicaSet", "ownerName": "coredns-abc12"}]
    svc, _ = service(repo, FakeEnvironment(extra_pods=pods))
    plan = svc.plan(request())
    assert plan.can_approve and plan.acknowledgements_needed == ["other/Deployment/db"]

    with pytest.raises(ValidationRefusedError) as refused:
        svc.approve(request(), ApprovalInput(plan.digest))
    assert refused.value.code == "ACKNOWLEDGEMENT_MISSING"

    started = svc.approve(request(), ApprovalInput(plan.digest, acknowledged_workloads=("other/Deployment/db",)))
    assert finish(svc, started["runKey"])["status"] == "COMPLETED"
    assert repo.get_run(started["runKey"])["approval"]["acknowledgedWorkloads"] == ["other/Deployment/db"]


def test_docker_desktop_needs_its_explicit_acknowledgement(repo):
    svc, _ = service(repo, FakeEnvironment(kind=DOCKER_DESKTOP, name="desktop", readiness=readiness()))
    plan = svc.plan(request())

    with pytest.raises(ValidationRefusedError) as refused:
        svc.approve(request(), ApprovalInput(plan.digest))
    assert refused.value.code == "DESKTOP_ACKNOWLEDGEMENT_REQUIRED"

    started = svc.approve(request(), ApprovalInput(plan.digest, desktop_acknowledged=True))
    assert repo.get_run(started["runKey"])["approval"]["desktopAcknowledged"] is True


def test_a_workflow_check_that_does_not_pass_now_stops_the_approval(repo):
    svc, _ = service(repo, FakeEnvironment(probe_survives=False))
    req = request(probe=PROBE)
    plan = svc.plan(req)

    with pytest.raises(ValidationRefusedError) as refused:
        svc.approve(req, ApprovalInput(plan.digest))
    assert refused.value.code == "PROBE_FAILED" and "503" in refused.value.message


def test_the_probe_can_be_tested_before_a_plan_is_approved(repo):
    svc, _ = service(repo)

    assert svc.probe_test(PROBE)["survives"] is True


def test_a_second_run_cannot_start_while_one_is_running_or_needs_a_person(repo):
    svc, env = service(repo)
    started = approve(svc, request())
    finish(svc, started["runKey"])
    repo.set_status(started["runKey"], RunStatus.NEEDS_MANUAL_RESTORE)

    plan = svc.plan(request())
    assert not plan.can_approve and "RUN_BLOCKED" in {i.code for i in plan.blocking}


# --- watching, history, proof, recovery ------------------------------------------------------------------------------

def test_the_current_run_survives_a_new_service_object_which_is_what_a_reload_or_restart_sees(repo):
    svc, env = service(repo)
    started = approve(svc, request())
    finish(svc, started["runKey"])

    fresh, _ = service(repo, env)
    view = fresh.current()

    assert view["runKey"] == started["runKey"] and view["status"] == "COMPLETED" and view["result"]["winner"]
    assert fresh.history()[0]["winner"] == view["result"]["winner"]


def test_a_backend_restart_recovers_a_live_run_and_never_kills_a_node(repo):
    svc, env = service(repo)
    started = approve(svc, request())
    finish(svc, started["runKey"])
    repo.set_status(started["runKey"], RunStatus.RUNNING)
    repo.save_restore_points(started["runKey"], {"reviews": {"replicas": 3}})
    kills_before = repo.used(started["runKey"])
    env.recoveries.clear()

    fresh, _ = service(repo, env)
    fresh.ensure_recovered()

    run = repo.get_run(started["runKey"])
    assert run["status"] == "INTERRUPTED" and run["recovery"]["complete"] is True and repo.used(started["runKey"]) == kills_before
    assert env.recoveries[0] == "restore_workers" and not any("fail" in str(call) for call in env.recoveries)


def test_the_approve_step_knows_whether_this_exact_change_was_proven_and_where(repo):
    svc, _ = service(repo)
    plan = svc.plan(request())
    sha = plan.candidates[0].loop_candidate.yaml_sha256
    assert svc.proof_for(namespace=NAMESPACE, workflow_id="productpage-view", candidate_sha256=sha)["state"] == "NOT_RUN"

    started = svc.approve(request(), ApprovalInput(plan.digest))
    finish(svc, started["runKey"])

    proof = svc.proof_for(namespace=NAMESPACE, workflow_id="productpage-view", candidate_sha256=sha)
    assert proof["state"] == "PROVEN" and proof["target"]["kind"] == DISPOSABLE and proof["preliminary"] is True
    assert svc.proof_for(namespace=NAMESPACE, workflow_id="productpage-view", candidate_sha256="different")["state"] == "OTHER_PROVEN"


def test_abort_of_a_run_that_is_not_running_is_refused(repo):
    svc, _ = service(repo)
    started = approve(svc, request())
    finish(svc, started["runKey"])

    with pytest.raises(ValidationRefusedError) as refused:
        svc.abort(started["runKey"])
    assert refused.value.code == "RUN_NOT_RUNNING"


def test_recovery_is_only_for_a_run_that_needs_manual_restoration(repo):
    svc, _ = service(repo)
    started = approve(svc, request())
    finish(svc, started["runKey"])

    with pytest.raises(ValidationRefusedError) as refused:
        svc.recover(started["runKey"])
    assert refused.value.code == "RUN_NOT_RECOVERABLE"
