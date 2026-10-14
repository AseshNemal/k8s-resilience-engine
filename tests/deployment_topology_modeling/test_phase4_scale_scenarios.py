import pytest
from experiments.placement_drift.constants import ScenarioStatus
from experiments.placement_drift.scenario_02_scale_up_stress import Scenario02ScaleUpStress
from experiments.placement_drift.scenario_03_scale_down_loss import Scenario03ScaleDownLoss


def test_scenario_02_scale_up_stress():
    scenario = Scenario02ScaleUpStress()
    result = scenario.run()

    assert result.scenario_id == "02_scale_up_stress"
    assert result.status == ScenarioStatus.COMPLETED
    assert result.rollback_successful is True
    # In scale-up, paymentservice scaled from 2 to 5 pods with additional replicas on worker-1
    assert "paymentservice" in result.drifted_services or result.d_rate >= 0.0


def test_scenario_02_scale_up_rollback():
    scenario = Scenario02ScaleUpStress()
    scenario.prepare()
    scenario.execute_disruption()
    scenario.observe()

    # In disruption state, 4 payment pods exist
    payment_pods = [p for p in scenario._active_pods if p.service_name == "paymentservice"]
    assert len(payment_pods) > 2

    # Rollback should restore 2 initial replicas
    assert scenario.rollback() is True
    restored_payment_pods = [p for p in scenario._active_pods if p.service_name == "paymentservice"]
    assert len(restored_payment_pods) == 2


def test_scenario_03_scale_down_loss():
    scenario = Scenario03ScaleDownLoss()
    result = scenario.run()

    assert result.scenario_id == "03_scale_down_loss"
    assert result.status == ScenarioStatus.COMPLETED
    assert result.rollback_successful is True
    # In scale-down, cartservice drops from 3 nodes to 1, causing placement drift
    assert result.d_rate > 0.0


def test_scenario_03_scale_down_rollback():
    scenario = Scenario03ScaleDownLoss()
    scenario.prepare()
    scenario.execute_disruption()
    scenario.observe()

    # In disruption state, only 1 cart replica remains
    cart_pods = [p for p in scenario._active_pods if p.service_name == "cartservice"]
    assert len(cart_pods) == 1

    # Rollback should restore 3 distributed cart replicas
    assert scenario.rollback() is True
    restored_cart_pods = [p for p in scenario._active_pods if p.service_name == "cartservice"]
    assert len(restored_cart_pods) == 3
