"""The Kubernetes-backed executor: every safety decision, driven against an in-memory gateway. No cluster is contacted."""

import pytest

from app.engine.recommendation_evaluation_validation.validation import (
    APPLY,
    FAIL_NODE,
    RunState,
    ValidationPlan,
    ValidationRefusedError,
    ValidationRun,
    authorize,
)
from app.engine.recommendation_evaluation_validation.validation.cluster_gateway import REAL_CLUSTER, TEST_DOUBLE
from app.engine.recommendation_evaluation_validation.validation.node_fault import NodeFaultKind
from tests.support.member4_harness import bookinfo_scenario, make_node
from tests.support.member4_k8s_setup import Setup, designation

def refused(code, build):
    with pytest.raises(ValidationRefusedError) as raised:
        build()
    assert raised.value.code == code, raised.value
    return raised.value


# --- who the cluster is ---------------------------------------------------------------------------------------------

def test_connecting_verifies_the_cluster_and_captures_the_baseline_without_writing():
    s = Setup()

    executor = s.executor()

    assert executor.target.name == "kind-resilience-test" and executor.target.environment == "test"
    assert [svc.name for svc in executor.baseline_graph.services] == [svc.name for svc in s.graph.services]
    assert s.gateway.wrote is False and s.fault.calls == []


def test_a_cluster_that_is_not_the_designated_one_is_refused_before_anything_is_read_but_its_identity():
    s = Setup(gateway_options=dict(uid="00000000-0000-4000-8000-000000000000"))

    refused("CLUSTER_IDENTITY_MISMATCH", s.executor)

    assert s.gateway.calls == ["identity"] and s.gateway.wrote is False


def test_the_docker_desktop_research_cluster_is_refused():
    s = Setup(gateway_options=dict(context="docker-desktop"))

    refused("RESEARCH_CLUSTER_REFUSED", s.executor)

    assert s.gateway.calls == ["identity"]


def test_a_cluster_whose_identity_cannot_be_read_is_refused():
    refused("CLUSTER_IDENTITY_UNVERIFIED", Setup(gateway_options=dict(unreadable_uid=True)).executor)


def test_a_cordon_and_drain_injector_cannot_be_used_for_a_failure_experiment():
    s = Setup(fault_options=dict(kind=NodeFaultKind.CORDON_DRAIN))

    refused("FAULT_NOT_A_NODE_FAILURE", s.executor)

    assert s.gateway.calls == []      # refused before the cluster was even asked who it is


def test_a_cluster_that_is_not_in_a_clean_steady_state_is_not_a_baseline():
    s = Setup()
    s.gateway.not_ready.add("w3")
    refused("BASELINE_UNHEALTHY", s.executor)

    s = Setup()
    s.gateway.graph.services[0].pods[0].ready = False
    refused("BASELINE_UNHEALTHY", s.executor)


def test_too_few_worker_nodes_to_survive_a_failure_is_refused():
    scenario = bookinfo_scenario("concentrated")
    one_node = scenario.deployment.model_copy(update={"nodes": scenario.deployment.nodes[:1]})

    refused("TOPOLOGY_TOO_SMALL", Setup(graph=one_node).executor)


def test_the_evidence_class_is_real_only_when_both_the_gateway_and_the_injector_act_on_a_real_cluster():
    s = Setup()
    assert s.executor().evidence_class == TEST_DOUBLE

    s.gateway.evidence_class = REAL_CLUSTER
    assert s.executor().evidence_class == TEST_DOUBLE      # the injector is still a double

    s.fault.evidence_class = REAL_CLUSTER
    assert s.executor().evidence_class == REAL_CLUSTER


# --- writing -----------------------------------------------------------------------------------------------------------

def test_writes_are_off_unless_they_were_turned_on_deliberately():
    s = Setup(executor_options=dict(writes_enabled=False))
    _, _, text = s.proposal()
    executor = s.executor()

    refused("WRITES_DISABLED", lambda: executor.apply(text))
    refused("WRITES_DISABLED", lambda: executor.fail_node("w1"))

    assert s.gateway.wrote is False and s.fault.calls == []


def test_the_proposal_is_checked_against_the_cluster_before_the_first_write():
    s = Setup()
    _, _, text = s.proposal()
    executor = s.executor()

    refused("PROPOSAL_NOT_ALLOWED", lambda: executor.apply(text.replace("spec:\n  template:", "spec:\n  paused: true\n  template:", 1)))
    refused("PROPOSAL_UNKNOWN_DEPLOYMENT", lambda: executor.apply(text.replace("name: reviews", "name: ghost", 1)))

    assert s.gateway.wrote is False


def test_a_deployment_edited_after_the_baseline_was_captured_is_not_patched():
    s = Setup()
    _, _, text = s.proposal()
    executor = s.executor()
    live = s.gateway.deployments["reviews"]
    s.gateway.deployments["reviews"] = type(live)(**{**live.__dict__, "resource_version": "rv-edited"})

    refused("DEPLOYMENT_DRIFTED", lambda: executor.apply(text))

    assert s.gateway.wrote is False


def test_the_cluster_is_asked_who_it_is_again_before_every_write():
    s = Setup()
    _, _, text = s.proposal()
    executor = s.executor()
    s.gateway.uid = "11111111-0000-4000-8000-000000000000"      # the connection settings now point somewhere else

    refused("CLUSTER_IDENTITY_MISMATCH", lambda: executor.apply(text))
    refused("CLUSTER_IDENTITY_MISMATCH", lambda: executor.fail_node("w1"))

    assert s.gateway.wrote is False and s.fault.calls == []


def test_an_approved_patch_is_written_and_the_placement_is_read_back_from_the_cluster():
    s = Setup()
    _, _, text = s.proposal()
    executor = s.executor()

    executor.apply(text)
    assert executor.await_settled() is True
    placed = executor.observe()

    assert s.gateway.calls.count("patch:reviews") == 1
    assert sorted(p.node for svc in placed.services if svc.name == "reviews" for p in svc.pods) == ["w1", "w2", "w3"]


# --- settling is observed, not assumed ----------------------------------------------------------------------------------

def test_a_rollout_that_never_completes_does_not_settle():
    s = Setup(gateway_options=dict(settles=False))
    _, _, text = s.proposal()
    executor = s.executor(settle_timeout_s=60.0)
    executor.apply(text)

    assert executor.await_settled() is False and s.clock.now >= 60.0


def test_a_pod_the_scheduler_could_not_place_means_the_change_did_not_settle_even_if_the_rollout_says_complete():
    s = Setup(gateway_options=dict(pending_after_patch=True))
    _, _, text = s.proposal()
    executor = s.executor(settle_timeout_s=60.0)
    executor.apply(text)

    assert executor.await_settled() is False


# --- the failure ---------------------------------------------------------------------------------------------------------

def test_a_stopped_node_is_confirmed_not_ready_and_its_pods_are_observed_not_ready():
    s = Setup()
    executor = s.executor()

    graph = executor.fail_node("w1")

    assert s.fault.calls == ["inject:w1"] and s.gateway.node_ready("w1") is False
    assert all(not pod.ready for svc in graph.services for pod in svc.pods if pod.node == "w1")
    assert any(pod.ready for svc in graph.services for pod in svc.pods if pod.node != "w1")


def test_a_stop_that_did_not_take_is_an_experiment_that_did_not_happen_and_not_a_measurement():
    s = Setup(fault_options=dict(works=False))
    executor = s.executor(failure_detect_timeout_s=30.0)

    with pytest.raises(RuntimeError, match="did not become NotReady"):
        executor.fail_node("w1")


@pytest.mark.parametrize("node, code", [("ghost", "NODE_NOT_ELIGIBLE"), ("cp", "NODE_NOT_ELIGIBLE")])
def test_only_a_ready_worker_of_the_baseline_may_be_failed(node, code):
    scenario = bookinfo_scenario("concentrated")
    nodes = [*scenario.deployment.nodes, make_node("cp", labels={"node-role.kubernetes.io/control-plane": ""})]
    s = Setup(graph=scenario.deployment.model_copy(update={"nodes": nodes}))
    executor = s.executor()

    refused(code, lambda: executor.fail_node(node))

    assert s.fault.calls == []


def test_a_node_that_does_not_come_back_is_reported_not_swallowed():
    s = Setup(fault_options=dict(restores=False))
    executor = s.executor(restore_timeout_s=30.0)
    executor.fail_node("w1")

    with pytest.raises(RuntimeError, match="did not become Ready"):
        executor.restore_node("w1")


def test_restoring_a_node_waits_until_it_reports_ready():
    s = Setup()
    executor = s.executor()
    executor.fail_node("w1")

    executor.restore_node("w1")

    assert s.gateway.node_ready("w1") is True and s.fault.calls == ["inject:w1", "restore:w1"]


# --- putting the workloads back --------------------------------------------------------------------------------------------

def test_revert_returns_the_deployments_and_the_pods_to_what_they_were_and_waits_for_it():
    s = Setup()
    _, _, text = s.proposal()
    executor = s.executor()
    before = s.gateway.deployments["reviews"]
    executor.apply(text)

    executor.revert()

    assert s.gateway.deployments["reviews"].topology_spread_constraints == before.topology_spread_constraints
    assert sorted(p.node for svc in s.gateway.graph.services if svc.name == "reviews" for p in svc.pods) == ["w1", "w1", "w1"]
    assert s.gateway.calls.index("restore:reviews") < len(s.gateway.calls) - 1      # and the rollout was awaited after it


def test_revert_with_nothing_written_touches_nothing():
    s = Setup()
    executor = s.executor()

    executor.revert()

    assert s.gateway.wrote is False


def test_a_revert_that_the_api_server_refuses_raises_and_keeps_the_restore_points():
    s = Setup(gateway_options=dict(fail_at="restore"))
    _, _, text = s.proposal()
    executor = s.executor()
    executor.apply(text)

    with pytest.raises(RuntimeError, match="refused the restore"):
        executor.revert()
    s.gateway.fail_at = None
    executor.revert()      # still possible: nothing was forgotten

    assert s.gateway.deployments["reviews"].topology_spread_constraints == ()


# --- the whole run through the executor ----------------------------------------------------------------------------------------

def run_through(s, **executor_options):
    approval, live, _ = s.proposal()
    executor = s.executor(**executor_options)
    plan = ValidationPlan.from_approval(approval, live, s.scenario.workflow, executor.target)
    run = ValidationRun(plan, executor)
    return run, run.execute(authorize(plan, APPLY, "dev"), authorize(plan, FAIL_NODE, "dev")), executor


def test_a_validation_run_through_the_kubernetes_executor_measures_and_leaves_the_cluster_as_it_found_it():
    s = Setup()

    run, result, executor = run_through(s)

    assert result.state is RunState.COMPLETED and result.validated is True
    assert result.workloads_reverted is True and result.needs_manual_restore is False
    assert s.gateway.node_ready("w1") is True
    assert sorted(p.node for svc in s.gateway.graph.services if svc.name == "reviews" for p in svc.pods) == ["w1", "w1", "w1"]
    assert result.comparison["placement"]["match"] is True      # the pods were observed where the model said


def test_a_run_whose_node_stop_did_not_take_ends_failed_with_no_verdict_and_the_workloads_put_back():
    s = Setup(fault_options=dict(works=False))

    run, result, _ = run_through(s, failure_detect_timeout_s=30.0)

    assert result.state is RunState.FAILED and result.validated is False and result.prediction_error is None
    assert result.workloads_reverted is True and "NotReady" in result.reasons[0]


def test_a_run_whose_pods_never_settle_is_inconclusive_and_never_validated():
    s = Setup(gateway_options=dict(pending_after_patch=True))

    run, result, _ = run_through(s, settle_timeout_s=60.0)

    assert result.state is RunState.INCONCLUSIVE and result.validated is False
    assert result.workloads_reverted is True and "node_ready:w1" not in s.gateway.calls


def test_a_run_whose_node_will_not_come_back_says_it_needs_a_manual_restore():
    s = Setup(fault_options=dict(restores=False))

    run, result, _ = run_through(s, restore_timeout_s=30.0)

    assert result.needs_manual_restore is True
    assert any(event["event"] == "RESTORE_FAILED" for event in run.events)


def test_a_run_with_writes_disabled_refuses_at_the_first_write_and_changes_nothing():
    s = Setup(executor_options=dict(writes_enabled=False))
    approval, live, _ = s.proposal()
    executor = s.executor()
    plan = ValidationPlan.from_approval(approval, live, s.scenario.workflow, executor.target)

    with pytest.raises(ValidationRefusedError) as raised:
        ValidationRun(plan, executor).execute(authorize(plan, APPLY, "dev"), authorize(plan, FAIL_NODE, "dev"))

    assert raised.value.code == "WRITES_DISABLED" and s.gateway.wrote is False and s.fault.calls == []
