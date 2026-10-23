"""A validation run leaves the test cluster as it found it, and its verdict does not change when it is asked twice."""

import pytest

from app.engine.recommendation_evaluation_validation.validation import (
    APPLY,
    FAIL_NODE,
    RunState,
    TestTarget,
    ValidationPlan,
    ValidationRefusedError,
    ValidationRun,
    authorize,
)
from tests.support.member4_approvals import approved_for_test
from tests.support.member4_harness import bookinfo_scenario
from tests.support.member4_test_cluster import FakeTestCluster

TARGET = TestTarget("kind-test", "test")


def prepare(**cluster_options):
    scenario = bookinfo_scenario("concentrated")
    approval, live = approved_for_test(scenario)
    plan = ValidationPlan.from_approval(approval, live, scenario.workflow, TARGET)
    return scenario, plan, FakeTestCluster(scenario.deployment, **cluster_options)


def grants(plan):
    return authorize(plan, APPLY, "dev"), authorize(plan, FAIL_NODE, "dev")


def reviews_nodes(graph):
    return sorted(pod.node for service in graph.services if service.name == "reviews" for pod in service.pods)


def test_the_applied_change_is_reverted_after_a_completed_run():
    scenario, plan, cluster = prepare()

    result = ValidationRun(plan, cluster).execute(*grants(plan))

    assert result.state is RunState.COMPLETED and result.workloads_reverted is True
    assert reviews_nodes(cluster.graph) == reviews_nodes(scenario.deployment) == ["w1", "w1", "w1"]
    assert cluster.calls.index("apply") < cluster.calls.index("revert")
    assert cluster.calls.index("restore:w1") < cluster.calls.index("revert")      # the node is back before the workloads move


@pytest.mark.parametrize("stage", ["settle", "failure"])
def test_the_applied_change_is_reverted_when_the_run_ends_early(stage):
    scenario, plan, cluster = prepare(fail_at=stage)

    result = ValidationRun(plan, cluster).execute(*grants(plan))

    assert result.state is RunState.FAILED and result.validated is False
    assert result.workloads_reverted is True and "revert" in cluster.calls
    assert reviews_nodes(cluster.graph) == ["w1", "w1", "w1"]


def test_a_write_that_failed_halfway_is_still_reverted():
    """The patch may have been partly written when it raised, so the revert runs for any attempted apply."""
    _, plan, cluster = prepare(fail_at="apply")

    result = ValidationRun(plan, cluster).execute(*grants(plan))

    assert result.state is RunState.FAILED and result.workloads_reverted is True and "revert" in cluster.calls


def test_a_revert_that_fails_is_reported_as_needing_a_manual_restore_and_never_swallowed():
    _, plan, cluster = prepare(revert_fails=True)

    result = ValidationRun(plan, cluster).execute(*grants(plan))

    assert result.state is RunState.COMPLETED          # the measurement was taken before the cleanup failed
    assert result.workloads_reverted is False and result.needs_manual_restore is True
    assert result.to_dict()["needsManualRestore"] is True and result.to_dict()["workloadsReverted"] is False


def test_nothing_is_reverted_when_nothing_was_applied():
    _, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)

    with pytest.raises(ValidationRefusedError):
        run.apply(authorize(plan, FAIL_NODE, "dev"))        # the wrong authorization is refused before any write
    result = run.finish()

    assert cluster.calls == [] and result.workloads_reverted is None


def test_asking_for_the_verdict_twice_gives_the_same_verdict():
    _, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)
    first = run.execute(*grants(plan))

    second = run.finish()

    assert second is first
    assert second.validated is True and second.observed == first.observed and cluster.calls.count("revert") == 1


def test_the_graphs_a_run_observed_are_kept_for_the_record():
    scenario, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)
    run.execute(*grants(plan))

    captured = run.captured

    assert set(captured) == {"baseline", "placed", "afterFailure"}
    assert reviews_nodes(captured["baseline"]) == ["w1", "w1", "w1"]      # before the change
    assert reviews_nodes(captured["placed"]) == ["w1", "w2", "w3"]        # where the scheduler put them
    assert all(not pod.ready for s in captured["afterFailure"].services for pod in s.pods if pod.node == "w1")
