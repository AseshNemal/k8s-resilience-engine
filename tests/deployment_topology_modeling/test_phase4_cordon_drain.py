import pytest
from experiments.placement_drift.constants import ScenarioStatus
from experiments.placement_drift.scenario_06_node_cordon import Scenario06NodeCordon
from experiments.placement_drift.scenario_07_node_drain_eviction import Scenario07NodeDrainEviction
from experiments.placement_drift.scenario_08_resource_contention import Scenario08ResourceContention


def test_scenario_06_node_cordon():
    scenario = Scenario06NodeCordon()
    res = scenario.run()

    assert res.scenario_id == "06_node_cordon"
    assert res.status == ScenarioStatus.COMPLETED
    assert res.rollback_successful is True
    # Cordoning worker-2 diverts scaled pods to worker-1
    assert res.d_rate >= 0.0


def test_scenario_06_node_cordon_rollback():
    scenario = Scenario06NodeCordon()
    scenario.prepare()
    scenario.execute_disruption()
    scenario.observe()

    # In disruption state, worker-2 is unschedulable
    worker_2 = next(n for n in scenario._cluster_nodes if n["name"] == "worker-2")
    assert worker_2["schedulable"] is False

    # Rollback should re-enable scheduling on worker-2
    assert scenario.rollback() is True
    worker_2_restored = next(n for n in scenario._cluster_nodes if n["name"] == "worker-2")
    assert worker_2_restored["schedulable"] is True


def test_scenario_07_node_drain_eviction():
    scenario = Scenario07NodeDrainEviction()
    res = scenario.run()

    assert res.scenario_id == "07_node_drain_eviction"
    assert res.status == ScenarioStatus.COMPLETED
    assert res.rollback_successful is True
    # Draining worker-1 evacuates its pods to worker-2 and worker-3
    assert res.d_rate > 0.0
    assert len(res.transitions) > 0
    # Check that a transition originated from worker-1
    drain_origins = [t.from_node for t in res.transitions]
    assert "worker-1" in drain_origins


def test_scenario_07_node_drain_rollback():
    scenario = Scenario07NodeDrainEviction()
    scenario.prepare()
    scenario.execute_disruption()
    scenario.observe()

    # In disruption state, worker-1 is marked drained
    worker_1 = next(n for n in scenario._cluster_nodes if n["name"] == "worker-1")
    assert worker_1["drained"] is True

    # Rollback should un-drain and restore pods to worker-1
    assert scenario.rollback() is True
    worker_1_restored = next(n for n in scenario._cluster_nodes if n["name"] == "worker-1")
    assert worker_1_restored["drained"] is False
    assert any(p.node_name == "worker-1" for p in scenario._active_pods)


def test_scenario_08_resource_contention():
    scenario = Scenario08ResourceContention()
    res = scenario.run()

    assert res.scenario_id == "08_resource_contention"
    assert res.status == ScenarioStatus.COMPLETED
    assert res.rollback_successful is True
    assert res.d_rate >= 0.0


def test_scenario_08_resource_contention_rollback():
    scenario = Scenario08ResourceContention()
    scenario.prepare()
    scenario.execute_disruption()
    scenario.observe()

    # In disruption state, worker-2 and worker-3 memory is exhausted (32MB)
    worker_2 = next(n for n in scenario._cluster_nodes if n["name"] == "worker-2")
    assert worker_2["mem_available_mb"] == 32

    # Rollback should restore full node capacity
    assert scenario.rollback() is True
    worker_2_restored = next(n for n in scenario._cluster_nodes if n["name"] == "worker-2")
    assert worker_2_restored["mem_available_mb"] == 4096
