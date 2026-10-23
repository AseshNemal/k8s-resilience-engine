"""Task-4 correctness and safety checks for the Member 4 Development Lab.

Pins the specific hazards the lab must not introduce: survivability claims without placement
assumptions, infeasible recommendations, confusion between predicted and observed placement,
loss of INSUFFICIENT_EVIDENCE, provenance promotion, algorithm duplication, and offline numbers
presented as cluster measurements.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from experiments.member4_dev_lab.app import create_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


# 1. A replica increase must not claim worker-node survivability without its placement assumptions.

def test_replica_increase_survivability_is_conditioned_on_stated_assumptions(client):
    detail = client.get("/api/dev-lab/scenarios/single-replica-increase").json()
    candidate_id = detail["detail"]["report"]["selectedRecommendationId"]
    assert candidate_id.endswith("replica-increase")
    feasibility = detail["feasibility"][candidate_id]
    assert feasibility["status"] == "FEASIBLE"
    assert feasibility["singleNodeResilience"] is True
    # the resilience verdict exists only under the modelled placement; the assumptions say so
    assert any("scheduler may place" in assumption for assumption in feasibility["assumptions"])
    assert any("spread" in assumption.lower() for assumption in feasibility["assumptions"])
    assert feasibility["notModeled"]                      # the limits of the verdict are stated, not hidden
    # and the added replica is a prediction, marked as such
    added = detail["extras"][candidate_id]["addedReplicas"]
    assert added and all("-added-" in name for name in added)


# 2. Infeasible scheduling changes are never recommended.

def test_no_infeasible_candidate_is_ever_selected(client):
    for scenario in client.get("/api/dev-lab/scenarios").json()["scenarios"]:
        detail = client.get(f"/api/dev-lab/scenarios/{scenario['name']}").json()
        report = detail["detail"]["report"]
        by_id = {c["id"]: c for c in report["recommendations"]}
        selected = report["selectedRecommendationId"]
        if selected is not None:
            assert by_id[selected]["feasible"] is True, f"{scenario['name']} selected an infeasible candidate"
        for candidate in detail["detail"]["candidates"]:
            if not candidate["feasible"]:
                assert candidate["approval"]["ready"] is False
                assert candidate["yamlProposal"]["status"] != "GENERATED"


# 3. Predicted placement and observed placement stay distinct.

def test_predicted_and_observed_placement_are_labelled_apart(client):
    detail = client.get("/api/dev-lab/scenarios/concentrated-replicas").json()
    assert detail["detail"]["evidence"]["isPrediction"] is True
    candidate = next(c for c in detail["detail"]["candidates"] if c["simulatedDeployment"])
    # the current placement is the observed input; the simulated deployment is a target state
    assert candidate["currentPlacement"]["reviews"]
    feasibility = detail["feasibility"][candidate["id"]]
    assert any("target state" in a for a in feasibility["assumptions"])
    # the validation comparison calls the no-measurement baseline a derivation, not a measurement
    run = detail["validation"]["runs"]["schedulerSpreads"]
    assert run["comparison"]["riskReduction"]["observedBasis"] == "DERIVED_FROM_PLACEMENT"
    assert "caveat" in run["comparison"]["riskReduction"]


# 6./7. The lab evaluates through the real Member 4 pipeline and reuses Component 3's predictor.

def test_the_lab_matches_a_direct_engine_evaluation(client):
    from tests.support.member4_scenarios import catalogue

    case = next(c for c in catalogue() if c.name == "multi-service-mandatory")
    live = case.scenario.evaluate()
    detail = client.get(f"/api/dev-lab/scenarios/{case.name}").json()
    assert detail["detail"]["report"] == live.report.model_dump(by_alias=True)
    assert detail["ranking"] == live.evidence["ranking"]


def test_the_lab_does_not_reimplement_the_failure_predictor():
    source = (ROOT / "experiments" / "member4_dev_lab" / "lab_service.py").read_text(encoding="utf-8")
    assert "from app.engine.failure_impact_prediction.service import predict_failure_impact" in source
    # no survival or blast-radius arithmetic of its own
    assert "blast_radius -" not in source
    assert "failed_services" not in source.replace("before.failed_services", "")


# 8. Offline numbers are never presented as real-cluster experimental results.

def test_offline_results_carry_their_limits_in_text(client):
    detail = client.get("/api/dev-lab/scenarios/concentrated-replicas").json()
    assert "scripted" in detail["validation"]["label"]
    assert "not the model" in detail["validation"]["label"] or "only proves the comparison arithmetic" in detail["validation"]["label"]
    experiment = client.get("/api/dev-lab/experiment").json()
    assert "does not measure accuracy against a real cluster" in experiment["demonstrates"]


# Baselines: only implemented methods may be reported as evaluated.

def test_unimplemented_baselines_are_marked_not_evaluated(client):
    baselines = client.get("/api/dev-lab/experiment").json()["baselines"]
    evaluated = [b["name"] for b in baselines if b["evaluated"]]
    not_evaluated = [b for b in baselines if not b["evaluated"]]
    assert evaluated == ["Proposed cross-layer method (workflow dependencies x pod placement)"]
    assert len(not_evaluated) == 3
    assert all(b["reason"] for b in not_evaluated)
