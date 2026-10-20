"""
Tests for the scenario ensemble (statistics over executed scenarios only).

Hand-computed D_rate per scripted scenario (share of services whose exposure status changed):
  01 initial        : nothing changes                                   -> 0
  02 scale-up       : payment {w1,w2} -> {w1,w2} still spread            -> 0
  03 scale-down     : cart 3 nodes -> only w1 (exposed); frontend same   -> 1/2
  04 rolling update : payment {w1,w2} -> {w2,w2}; checkout same          -> 1/2
  05 pod deletion   : payment {w1,w2} -> {w1,w1}; email same             -> 1/2
  06 cordon         : payment {w1,w2} -> {w1,w2,w1} still spread         -> 0
  07 drain          : payment {w1,w2} -> {w2,w2}; cart, checkout same    -> 1/3
  08 contention     : payment stays on w1 and w2                         -> 0
mean = (3 * 0.5 + 0.3333) / 8 = 0.2292;  s (n-1) = 0.2510;
95% CI = 0.2292 +/- 2.365 * 0.2510 / sqrt(8) = [0.0193, 0.4390]
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from experiments.placement_drift.constants import ScenarioExecutionMode
from experiments.placement_drift.exceptions import ScenarioExecutionError
from experiments.placement_drift.parametric_ensemble_evaluator import (
    ParametricEnsembleEvaluator,
    EnsembleBenchmarkMetrics,
    t_critical_95,
)
from experiments.placement_drift.scenario_01_initial_deployment import Scenario01InitialDeployment

EXPECTED = {
    "01_initial_deployment": 0.0, "02_scale_up_stress": 0.0, "03_scale_down_loss": 0.5,
    "04_rolling_update": 0.5, "05_pod_deletion_chaos": 0.5, "06_node_cordon": 0.0,
    "07_node_drain_eviction": 0.3333, "08_resource_contention": 0.0,
}


@pytest.fixture
def client():
    return TestClient(app)


def test_every_trial_comes_from_an_executed_scenario():
    metrics = ParametricEnsembleEvaluator().run_ensemble()
    assert isinstance(metrics, EnsembleBenchmarkMetrics)
    assert metrics.total_trials == 8
    assert {t.trial_id: t.d_rate for t in metrics.trials} == pytest.approx(EXPECTED, abs=1e-4)


def test_ensemble_statistics_hand_computed():
    metrics = ParametricEnsembleEvaluator().run_ensemble()
    assert metrics.measured_trials == 8
    assert metrics.mean_d_rate == pytest.approx(0.2292, abs=1e-3)
    assert metrics.std_dev_d_rate == pytest.approx(0.2510, abs=1e-3)
    assert metrics.confidence_interval_95 == pytest.approx([0.0193, 0.4390], abs=1e-3)
    assert metrics.min_d_rate == pytest.approx(0.0)
    assert metrics.max_d_rate == pytest.approx(0.5)


def test_dry_run_is_never_labelled_as_evidence():
    metrics = ParametricEnsembleEvaluator().run_ensemble()
    assert metrics.execution_mode == ScenarioExecutionMode.DRY_RUN_SIMULATION
    assert metrics.is_experimental_evidence is False
    assert "scripted" in metrics.evidence_note
    assert "scripted" in ParametricEnsembleEvaluator.generate_markdown_report(metrics)


def test_live_mode_is_refused_for_scripted_scenarios():
    with pytest.raises(ScenarioExecutionError):
        Scenario01InitialDeployment(execution_mode=ScenarioExecutionMode.LIVE_CLUSTER)


def test_t_critical_values():
    assert t_critical_95(1) == pytest.approx(12.706)
    assert t_critical_95(7) == pytest.approx(2.365)
    assert t_critical_95(30) == pytest.approx(2.042)
    assert t_critical_95(200) == pytest.approx(1.960)


def test_api_get_ensemble_endpoints(client):
    data = client.get("/api/deployments/benchmarks/ensemble").json()
    assert data["total_trials"] == 8
    assert data["is_experimental_evidence"] is False
    res_md = client.get("/api/deployments/benchmarks/ensemble/markdown")
    assert res_md.status_code == 200
    assert "text/markdown" in res_md.headers.get("content-type", "")
    assert "# Placement Drift Ensemble Summary" in res_md.text
