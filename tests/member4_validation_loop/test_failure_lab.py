"""The Failure lab: the person's own choice of workers, stopped together, held, restored, and recorded. Doubles only; nothing real is stopped."""

import pytest

from app.engine.recommendation_evaluation_validation.validation import ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation_loop.grant import GrantAuthority, GrantedCandidate, InMemoryKillBudget, new_grant
from app.engine.recommendation_evaluation_validation.validation_loop.lab import group_label, placement_impact
from app.engine.recommendation_evaluation_validation.validation_loop.loop import KillBlocked, ValidationLoop
from app.engine.recommendation_evaluation_validation.validation_loop.models import Mode, ModePlan, RunStatus, resolve_mode
from app.engine.recommendation_evaluation_validation.validation_loop.service import ApprovalInput, PlanRequest, ValidationLoopService
from app.engine.recommendation_evaluation_validation.validation_loop.worker import LoopWorker
from tests.member4_validation_loop.conftest import NOW
from tests.member4_validation_loop.support import WORKERS
from tests.support.member4_harness import bookinfo_scenario
from tests.support.member4_test_cluster import FakeTestCluster
from tests.support.validation_loop_fake_environment import NAMESPACE, FakeEnvironment
from datetime import datetime, timezone


def lab_loop(nodes=("w1", "w2"), *, hold=12.0, max_nodes=None, cluster_options=None, before_kill=None, abort=lambda: False, sleeps=None):
    scenario = bookinfo_scenario("concentrated")
    plan = resolve_mode(Mode.LAB, WORKERS, lab_workers=list(nodes), hold_s=hold)
    cluster = FakeTestCluster(scenario.deployment, **(cluster_options or {}))
    grant = new_grant(run_id="lab-1", plan_digest_value="p" * 64, target_name=cluster.target.name, cluster_uid="uid-1", namespace="bookinfo",
                      workers=tuple(nodes[:max_nodes]) if max_nodes else plan.workers, candidates=(), repeats=1, measure_baseline=True,
                      approved_at=datetime(2026, 11, 19, tzinfo=timezone.utc))
    budget = InMemoryKillBudget()
    armed = []
    log = sleeps if sleeps is not None else []
    loop = ValidationLoop(workflow=None, plan=plan, authority=GrantAuthority(grant, budget), candidates=[], executor_factory=lambda _c: cluster,
                          arm=lambda auth, worker, digest: armed.append(worker), before_kill=before_kill, abort=abort,
                          sleep=lambda seconds: log.append(seconds))
    return loop, cluster, budget, armed


def test_every_chosen_worker_is_stopped_together_held_and_restored_together():
    loop, cluster, budget, armed = lab_loop(("w1", "w2"), hold=12.0)

    result = loop.run()

    assert result.status is RunStatus.COMPLETED and result.winner is None
    assert [c for c in cluster.calls if c.startswith(("fail", "restore"))] == ["fail:w1+w2", "restore:w1+w2"]
    assert armed == ["w1", "w2"] and budget.used("lab-1") == 2 == result.kills_used
    outcome = result.baseline[0]
    assert outcome.recovery["verified"] is True and outcome.predicted["heldS"] == 12.0
    assert "apply" not in "".join(cluster.calls)                   # a lab run applies nothing


def test_the_run_reports_what_the_placement_rule_says_beside_what_was_observed():
    loop, *_ = lab_loop(("w1",))

    outcome = loop.run().baseline[0]

    impact = outcome.predicted["placementImpact"]
    assert impact["nodes"] == ["w1"] and impact["down"] == outcome.predicted["servicesDown"]
    assert outcome.readiness["observed"] is not None and outcome.readiness["holds"] is None      # no verdict about a change is invented


def test_a_blocked_check_stops_nothing():
    def refuse(worker, candidate, **options):
        raise KillBlocked("ESSENTIAL_WORKLOAD", "something essential")
    loop, cluster, budget, armed = lab_loop(before_kill=refuse)

    result = loop.run()

    assert result.status is RunStatus.INTERRUPTED and "ESSENTIAL_WORKLOAD" in result.stop_reason
    assert not any(c.startswith("fail") for c in cluster.calls) and armed == [] and budget.used("lab-1") == 0


def test_a_worker_outside_the_approval_means_no_worker_is_stopped():
    loop, cluster, budget, armed = lab_loop(("w1", "w2"), max_nodes=1)         # the grant covers w1 only

    result = loop.run()

    assert result.status is RunStatus.INTERRUPTED and "GRANT_WORKER_NOT_APPROVED" in result.stop_reason
    assert not any(c.startswith("fail") for c in cluster.calls) and armed == []


def test_a_worker_that_cannot_be_restored_needs_a_person_and_is_never_called_restored():
    loop, *_ = lab_loop(cluster_options={"restore_fails": True})

    result = loop.run()

    assert result.status is RunStatus.NEEDS_MANUAL_RESTORE and result.needs_manual_restore
    assert result.baseline[0].recovery["verified"] is False


def test_ending_the_hold_early_restores_every_worker_and_says_so():
    ticks = {"n": 0}

    def abort():
        ticks["n"] += 1
        return ticks["n"] > 2
    loop, cluster, _, _ = lab_loop(hold=60.0, abort=abort)

    result = loop.run()

    assert result.status is RunStatus.INTERRUPTED and "ended early" in result.stop_reason
    assert "restore:w1+w2" in cluster.calls and result.baseline[0].recovery["verified"] is True
    assert result.baseline[0].predicted["heldS"] < 60.0


def test_a_failure_that_was_not_produced_is_still_restored_and_not_called_a_result():
    loop, cluster, *_ = lab_loop(cluster_options={"fail_at": "failure"})

    result = loop.run()

    assert "restore:w1+w2" in cluster.calls
    assert result.baseline[0].status.value == "INCONCLUSIVE" and result.baseline[0].reasons


# --- choosing the workers -----------------------------------------------------------------------------------------------

def test_the_person_can_choose_any_number_of_eligible_workers_but_never_none_or_a_stranger():
    assert resolve_mode(Mode.LAB, WORKERS, lab_workers=["w3", "w1"], hold_s=30).workers == ("w1", "w3")
    assert resolve_mode(Mode.LAB, WORKERS, lab_workers=WORKERS, hold_s=30).simultaneous is True       # all of them is allowed
    for bad in ([], ["control-plane"], ["w1", "w9"]):
        with pytest.raises(ValidationRefusedError):
            resolve_mode(Mode.LAB, WORKERS, lab_workers=bad, hold_s=30)


def test_the_hold_time_is_bounded():
    for hold in (1, 5000):
        with pytest.raises(ValidationRefusedError) as error:
            resolve_mode(Mode.LAB, WORKERS, lab_workers=["w1"], hold_s=hold)
        assert error.value.code == "HOLD_OUT_OF_RANGE"


def test_the_placement_rule_for_a_set_of_nodes():
    graph = bookinfo_scenario("concentrated").deployment
    both = placement_impact(graph, ["w1", "w2", "w3"])

    assert both["down"] and not any(s["state"] == "ok" for s in both["services"] if s["ready"])      # stopping every worker loses every service
    assert placement_impact(graph, [])["down"] == []
    assert len(group_label([f"worker-{i}-with-a-long-name" for i in range(8)])) <= 100


# --- through the service ----------------------------------------------------------------------------------------------------

def service(repo):
    env = FakeEnvironment()
    return ValidationLoopService(env, repo, LoopWorker(repo), clock=lambda: NOW, sleep=lambda seconds: None), env


def lab_request(nodes, hold=20.0):
    return PlanRequest(mode=Mode.LAB, workflow_id="lab", namespace=NAMESPACE, nodes=tuple(nodes), hold_s=hold)


def test_the_plan_lists_what_each_service_would_do_and_what_else_would_go_down_without_needing_a_workflow(repo):
    svc, _ = service(repo)

    plan = svc.plan(lab_request(["w1", "w2"]))

    data = plan.to_dict()
    assert data["lab"]["nodes"] == ["w1", "w2"] and data["lab"]["survivingWorkers"] == ["w3"] and data["mode"]["plannedFailures"] == 2
    assert data["mode"]["mode"] == "LAB" and data["candidates"] == [] and data["workflowBasis"] == "NONE"
    assert "kube-system" in " ".join(data["acknowledgementsNeeded"])          # DNS on both stopped workers: named, never silent


def test_essential_services_must_be_accepted_by_name_before_anything_is_stopped(repo):
    svc, env = service(repo)
    request = lab_request(["w1", "w2"])
    plan = svc.plan(request)

    with pytest.raises(ValidationRefusedError) as error:
        svc.approve(request, ApprovalInput(plan.digest))
    assert error.value.code == "ACKNOWLEDGEMENT_MISSING" and not env.cluster.calls

    started = svc.approve(request, ApprovalInput(plan.digest, acknowledged_workloads=tuple(plan.acknowledgements_needed)))
    svc._worker.join(15)
    view = svc.view(started["runKey"], research=True)

    assert view["status"] == "COMPLETED" and view["lab"]["finished"] and view["lab"]["nodes"] == ["w1", "w2"]
    assert "fail:w1+w2" in env.cluster.calls and "restore:w1+w2" in env.cluster.calls
    assert view["metrics"]["progress"] == {"done": 1, "total": 1}


def test_the_approval_carries_no_person(repo):
    svc, _ = service(repo)
    request = lab_request(["w3"])
    plan = svc.plan(request)

    started = svc.approve(request, ApprovalInput(plan.digest, acknowledged_workloads=tuple(plan.acknowledgements_needed)))
    svc._worker.join(15)

    grant = svc._repo.get_run(started["runKey"])["approval"]
    assert "approvedBy" not in grant and grant["workers"] == ["w3"] and grant["maxFailures"] == 1
