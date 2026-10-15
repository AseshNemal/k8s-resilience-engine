import pytest
from experiments.placement_drift.constants import ScenarioStatus
from experiments.placement_drift.scenario_04_rolling_update import Scenario04RollingUpdate


def test_scenario_04_rolling_update_execution():
    scenario = Scenario04RollingUpdate()
    res = scenario.run()

    assert res.scenario_id == "04_rolling_update"
    assert res.status == ScenarioStatus.COMPLETED
    assert res.rollback_successful is True
    # Rolling update churn causes v2 pods to co-locate on worker-2, shifting paymentservice placement
    assert res.d_rate > 0.0
    assert "paymentservice" in res.drifted_services


def test_scenario_04_rolling_update_rollback():
    scenario = Scenario04RollingUpdate()
    scenario.prepare()
    scenario.execute_disruption()
    scenario.observe()

    # Pre-rollback check
    assert any(p.pod_name.startswith("payment-v2") for p in scenario._active_pods)

    # Rollback execution
    success = scenario.rollback()
    assert success is True
    # Post-rollback should restore v1 baseline
    assert any(p.pod_name.startswith("payment-v1") for p in scenario._active_pods)
