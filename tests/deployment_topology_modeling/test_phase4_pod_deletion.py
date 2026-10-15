import pytest
from experiments.placement_drift.constants import ScenarioStatus
from experiments.placement_drift.scenario_05_pod_deletion_chaos import Scenario05PodDeletionChaos


def test_scenario_05_pod_deletion_chaos_execution():
    scenario = Scenario05PodDeletionChaos()
    res = scenario.run()

    assert res.scenario_id == "05_pod_deletion_chaos"
    assert res.status == ScenarioStatus.COMPLETED
    assert res.rollback_successful is True
    # In pod deletion, payment-2 is deleted from worker-2 and rescheduled to worker-1
    assert res.d_rate > 0.0
    assert "paymentservice" in res.drifted_services


def test_scenario_05_pod_deletion_rollback():
    scenario = Scenario05PodDeletionChaos()
    scenario.prepare()
    scenario.execute_disruption()
    scenario.observe()

    # In disruption state, payment-3 exists on worker-1
    pod_names = {p.pod_name for p in scenario._active_pods}
    assert "payment-3" in pod_names
    assert "payment-2" not in pod_names

    # Rollback
    assert scenario.rollback() is True
    restored_pod_names = {p.pod_name for p in scenario._active_pods}
    assert "payment-2" in restored_pod_names
    assert "payment-3" not in restored_pod_names
