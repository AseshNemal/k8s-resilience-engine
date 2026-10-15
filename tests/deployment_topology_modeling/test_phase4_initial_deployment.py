import pytest
from experiments.placement_drift.constants import DisruptionType, ScenarioStatus
from experiments.placement_drift.scenario_01_initial_deployment import Scenario01InitialDeployment
from experiments.placement_drift.snapshot_repository import SnapshotRepository


def test_scenario_01_baseline_steady_state():
    scenario = Scenario01InitialDeployment()
    result = scenario.run()

    assert result.scenario_id == "01_initial_deployment"
    assert result.disruption_type == DisruptionType.INITIAL_DEPLOYMENT
    assert result.status == ScenarioStatus.COMPLETED
    assert result.rollback_successful is True
    assert result.d_rate == pytest.approx(0.0)
    assert result.delta_e_rate == pytest.approx(0.0)
    assert len(result.transitions) == 0
    assert len(result.drifted_services) == 0


def test_scenario_01_snapshot_hashes_identical():
    repo = SnapshotRepository()
    scenario = Scenario01InitialDeployment(repository=repo)
    result = scenario.run()

    assert result.baseline_hash != ""
    assert result.target_hash != ""
    # In steady state with no mutation, baseline and target hashes are identical
    assert result.baseline_hash == result.target_hash

    t0 = repo.get("01_initial_deployment", "T0")
    t1 = repo.get("01_initial_deployment", "T1")
    assert t0 is not None
    assert t1 is not None
    assert t0.sha256_hash == t1.sha256_hash


def test_scenario_01_rollback_idempotent():
    scenario = Scenario01InitialDeployment()
    scenario.prepare()
    scenario.execute_disruption()
    scenario.observe()
    assert scenario.rollback() is True
