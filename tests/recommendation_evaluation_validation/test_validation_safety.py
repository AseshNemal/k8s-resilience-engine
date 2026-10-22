"""Validation never writes or breaks anything without an exact, current approval, and a failed run is never 'validated'."""

import dataclasses

import pytest

from app.engine.recommendation_evaluation_validation.validation import (
    APPLY,
    FAIL_BASELINE,
    FAIL_NODE,
    Authorization,
    RunState,
    TestTarget,
    ValidationPlan,
    ValidationRefusedError,
    ValidationRun,
    authorize,
    to_validation_report,
)
from app.shared.schemas.approval import ApprovalStatus
from tests.recommendation_evaluation_validation.test_validation_run import TARGET, grants, prepare, run_all
from tests.support.member4_approvals import as_live, requested_for_test
from tests.support.member4_harness import bookinfo_scenario
from tests.support.member4_test_cluster import FakeTestCluster


def refused(code, call, *args, **kwargs):
    with pytest.raises(ValidationRefusedError) as raised:
        call(*args, **kwargs)
    assert raised.value.code == code
    return raised.value


# --- the plan: only a current, approved, test-targeted proposal ---------------------------------------------------

def test_a_request_that_was_never_approved_cannot_be_validated():
    scenario = bookinfo_scenario("concentrated")
    service, live, rec_id = requested_for_test(scenario)

    refused("APPROVAL_NOT_APPROVED", ValidationPlan.from_approval, service.get_approval(rec_id), live, scenario.workflow, TARGET)


def test_a_rejected_request_cannot_be_validated():
    scenario = bookinfo_scenario("concentrated")
    service, live, rec_id = requested_for_test(scenario)
    rejected = service.reject(rec_id, "v1", "no")

    assert rejected.status is ApprovalStatus.REJECTED
    refused("APPROVAL_NOT_APPROVED", ValidationPlan.from_approval, rejected, live, scenario.workflow, TARGET)


def test_only_a_test_environment_is_ever_a_target():
    scenario, approval, live, _, _ = prepare()

    refused("TARGET_NOT_TEST_ENVIRONMENT", ValidationPlan.from_approval, approval, live, scenario.workflow,
            TestTarget("production", "production"))


def test_an_approval_the_topology_has_overtaken_is_stale():
    scenario, approval, _, _, _ = prepare()
    drifted = bookinfo_scenario("concentrated")
    reviews = next(s for s in drifted.deployment.services if s.name == "reviews")
    reviews.pods.append(reviews.pods[0].model_copy(update={"name": "reviews-3"}))      # a fourth replica on w1
    reviews.declared_replicas = reviews.ready_replicas = 4
    now = as_live(drifted.evaluate())

    error = refused("APPROVAL_STALE", ValidationPlan.from_approval, approval, now, scenario.workflow, TARGET)

    assert "no longer" in error.message


def test_an_approval_whose_yaml_changed_is_stale_even_when_the_plan_did_not():
    scenario, approval, live, _, _ = prepare()
    for svc in live.details["currentDeployment"]["services"]:
        if svc["name"] == "reviews":
            svc["deploymentNames"] = ["reviews-v2"]

    refused("APPROVAL_STALE", ValidationPlan.from_approval, approval, live, scenario.workflow, TARGET)


def test_an_approval_that_goes_stale_between_planning_and_applying_writes_nothing():
    scenario, approval, live, plan, cluster = prepare()
    drifted = bookinfo_scenario("concentrated")
    next(s for s in drifted.deployment.services if s.name == "reviews").pods.pop()       # a replica disappeared
    run = ValidationRun(plan, cluster, approval=approval, current_evaluation=lambda: as_live(drifted.evaluate()))
    apply_auth, _ = grants(plan)

    refused("APPROVAL_STALE", run.apply, apply_auth)

    assert cluster.calls == [] and run.state is RunState.PLANNED


# --- explicit authorization for every write and every controlled failure ------------------------------------------

def test_no_step_runs_without_a_named_person_authorizing_it():
    _, _, _, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)

    refused("AUTHORIZATION_MISSING", run.apply, None)
    refused("AUTHORIZATION_MISSING", run.apply, Authorization(APPLY, "  ", plan.digest, plan.target.name))

    assert cluster.calls == [] and cluster.wrote is False


def test_an_authorization_for_one_action_cannot_be_used_for_another():
    _, _, _, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)

    refused("AUTHORIZATION_MISMATCH", run.apply, authorize(plan, FAIL_NODE, "dev"))

    assert cluster.wrote is False


def test_an_authorization_for_another_plan_or_target_is_refused():
    _, _, _, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)

    refused("AUTHORIZATION_MISMATCH", run.apply, Authorization(APPLY, "dev", "0" * 64, plan.target.name))
    refused("AUTHORIZATION_MISMATCH", run.apply, Authorization(APPLY, "dev", plan.digest, "some-other-cluster"))

    assert cluster.wrote is False


def test_an_executor_pointed_at_a_different_cluster_than_the_plan_is_refused():
    scenario, _, _, plan, _ = prepare()
    elsewhere = FakeTestCluster(scenario.deployment, name="another-cluster")

    refused("TARGET_MISMATCH", ValidationRun(plan, elsewhere).apply, authorize(plan, APPLY, "dev"))

    assert elsewhere.calls == []


def test_an_executor_that_is_not_a_test_environment_is_refused_even_with_a_matching_plan():
    scenario, approval, live, _, _ = prepare()
    plan = ValidationPlan.from_approval(approval, live, scenario.workflow, TARGET)
    sneaky = FakeTestCluster(scenario.deployment, name="kind-test", environment="staging")
    sneaky.target = TestTarget("kind-test", "staging")
    plan.target = sneaky.target          # even a plan edited after the fact cannot reach a non-test environment

    refused("TARGET_NOT_TEST_ENVIRONMENT", ValidationRun(plan, sneaky).apply, authorize(plan, APPLY, "dev"))

    assert sneaky.calls == []


def test_steps_cannot_be_taken_out_of_order():
    _, _, _, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)

    refused("WRONG_STATE", run.inject_failure, authorize(plan, FAIL_NODE, "dev"))
    refused("WRONG_STATE", run.observe_placement)

    assert cluster.calls == []


def test_a_failure_is_never_injected_before_the_change_has_been_observed():
    _, _, _, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)
    run.apply(authorize(plan, APPLY, "dev"))

    refused("WRONG_STATE", run.inject_failure, authorize(plan, FAIL_NODE, "dev"))

    assert not any(call.startswith("fail:") for call in cluster.calls)


def test_the_audit_trail_names_who_authorized_each_step_in_order():
    _, _, _, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)

    run.execute(authorize(plan, APPLY, "alice"), authorize(plan, FAIL_NODE, "bob"))

    authorized = [e["detail"] for e in run.events if e["event"] == "AUTHORIZED"]
    assert authorized == [f"{APPLY} by alice", f"{FAIL_NODE} by bob"]
    assert [e["event"] for e in run.events][-1] == "COMPLETED"


# --- a run that goes wrong is never validated ------------------------------------------------------------------

def test_a_lost_connection_while_applying_interrupts_the_run_and_validates_nothing():
    _, _, _, plan, cluster = prepare(interrupt_at="apply")

    result = run_all(plan, cluster)

    assert result.state is RunState.INTERRUPTED and result.validated is False and result.comparison == {}
    assert "connection lost" in result.reasons[0]
    assert to_validation_report(result) is None


def test_an_error_while_applying_fails_the_run():
    _, _, _, plan, cluster = prepare(fail_at="apply")

    result = run_all(plan, cluster)

    assert result.state is RunState.FAILED and result.validated is False and "apply failed" in result.reasons[0]


def test_a_rollout_that_never_settles_is_inconclusive_and_no_failure_is_injected():
    _, _, _, plan, cluster = prepare(settles=False)

    result = run_all(plan, cluster)

    assert result.state is RunState.INCONCLUSIVE and result.validated is False
    assert not any(call.startswith("fail:") for call in cluster.calls)
    assert "did not settle" in result.reasons[0]


def test_an_interrupted_failure_still_restores_the_node():
    _, _, _, plan, cluster = prepare(interrupt_at="failure")

    result = run_all(plan, cluster)

    assert result.state is RunState.INTERRUPTED and result.validated is False
    assert "restore:w1" in cluster.calls and cluster.down == set()


def test_a_node_that_cannot_be_restored_is_flagged_loudly_not_swallowed():
    _, _, _, plan, cluster = prepare(restore_fails=True)

    result = run_all(plan, cluster)

    assert result.needs_manual_restore is True and cluster.down == {"w1"}
    assert result.to_dict()["needsManualRestore"] is True


def test_an_observation_that_cannot_be_read_is_inconclusive_not_a_guess():
    class Ghost(FakeTestCluster):
        def fail_node(self, node):
            graph = super().fail_node(node)
            graph.services[0].pods[0].node = "ghost-node"      # a pod on a node the topology does not know
            return graph

    scenario, _, _, plan, _ = prepare()
    cluster = Ghost(scenario.deployment)

    result = run_all(plan, cluster)

    assert result.state is RunState.INCONCLUSIVE and result.validated is False and "could not be evaluated" in result.reasons[0]
    assert to_validation_report(result) is None


def test_a_run_stopped_early_reports_where_it_stopped():
    _, _, _, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)
    run.apply(authorize(plan, APPLY, "dev"))

    result = run.finish()

    assert result.state is RunState.INCONCLUSIVE and result.validated is False and "APPLIED" in result.reasons[0]


def test_nothing_in_the_validation_package_can_reach_a_cluster_by_itself():
    from pathlib import Path
    import app.engine.recommendation_evaluation_validation.validation as package

    source = "\n".join(p.read_text() for p in Path(package.__file__).parent.glob("*.py"))

    for forbidden in ("subprocess", "kubectl", "kubeconfig", "app.integrations", "httpx", "socket", "os.system"):
        assert forbidden not in source, forbidden
