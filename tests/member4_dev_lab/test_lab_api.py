"""Tests for the Member 4 Development Lab API (offline, development only).

These tests pin what the lab promises: the 17 controlled scenarios, deterministic evaluation through the
real Member 4 pipeline, preserved feasibility/INSUFFICIENT_EVIDENCE behaviour, YAML that is never
applicable from offline evidence, scripted-validation results, and correct error handling.
"""

import pytest
from fastapi.testclient import TestClient

from experiments.member4_dev_lab.app import create_app
from tests.support.member4_scenarios import catalogue


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_the_lab_lists_exactly_the_controlled_scenarios(client):
    body = client.get("/api/dev-lab/scenarios").json()
    assert [s["name"] for s in body["scenarios"]] == [case.name for case in catalogue()]
    assert len(body["scenarios"]) == 17
    for scenario in body["scenarios"]:
        assert scenario["objective"]
        assert set(scenario["expected"]) == {"feasibility", "yaml", "selected", "documents"}


def test_everything_the_lab_serves_is_labelled_offline(client):
    assert "OFFLINE" in client.get("/api/dev-lab/scenarios").json()["label"]
    detail = client.get("/api/dev-lab/scenarios/concentrated-replicas").json()
    assert "OFFLINE" in detail["label"]
    assert "OFFLINE" in client.get("/api/dev-lab/experiment").json()["label"]


@pytest.mark.parametrize("name", ["concentrated-replicas", "taints-unread", "multi-service-mandatory"])
def test_scenario_evaluation_is_deterministic(client, name):
    first = client.get(f"/api/dev-lab/scenarios/{name}")
    second = client.get(f"/api/dev-lab/scenarios/{name}")
    assert first.status_code == 200
    assert first.json() == second.json()


def test_the_concentrated_scenario_walks_the_whole_pipeline(client):
    detail = client.get("/api/dev-lab/scenarios/concentrated-replicas").json()

    risk = detail["currentRisk"]
    assert risk["failedNode"] == "w1"
    assert risk["blastRadius"] == pytest.approx(100.0)
    assert "reviews" in risk["failedServices"]
    assert risk["failedWorkflows"] == ["productpage-view"]
    assert any(cause["type"] == "REPLICA_CONCENTRATION" for cause in risk["rootCauses"])

    report = detail["detail"]["report"]
    assert report["selectedRecommendationId"] == "rec-productpage-view-reviews-topology-spread"
    selected = next(c for c in report["recommendations"] if c["id"] == report["selectedRecommendationId"])
    assert selected["feasible"] is True
    assert selected["predictedBlastRadius"] < risk["blastRadius"]
    assert selected["riskReduction"] == pytest.approx(risk["blastRadius"] - selected["predictedBlastRadius"])
    assert selected["score"] > 0

    ranked = detail["ranking"]["candidates"][selected["id"]]
    assert ranked["rank"] == 1
    assert ranked["components"]["blastBefore"] == pytest.approx(risk["blastRadius"])


def test_feasibility_refusals_are_preserved_not_hidden(client):
    detail = client.get("/api/dev-lab/scenarios/insufficient-cpu").json()
    candidates = detail["detail"]["candidates"]
    assert candidates
    assert all(not c["feasible"] for c in candidates)
    assert all(c["feasibility"]["status"] == "INFEASIBLE" for c in candidates)
    assert detail["detail"]["report"]["selectedRecommendationId"] is None
    assert detail["detail"]["yaml"]["available"] is False


def test_insufficient_evidence_stays_insufficient_evidence(client):
    detail = client.get("/api/dev-lab/scenarios/taints-unread").json()
    candidates = detail["detail"]["candidates"]
    assert candidates
    assert all(c["feasibility"]["status"] == "INSUFFICIENT_EVIDENCE" for c in candidates)
    assert any(c["feasibility"]["missingEvidence"] for c in candidates)
    assert detail["detail"]["report"]["selectedRecommendationId"] is None
    assert detail["detail"]["yaml"]["available"] is False
    assert detail["validation"]["available"] is False


def test_a_scenario_with_nothing_to_fix_generates_no_candidates(client):
    detail = client.get("/api/dev-lab/scenarios/correctly-distributed").json()
    assert detail["detail"]["outcome"]["state"] == "NO_CANDIDATES"
    assert detail["detail"]["candidates"] == []


def test_yaml_is_generated_for_the_selection_but_never_applicable_offline(client):
    detail = client.get("/api/dev-lab/scenarios/concentrated-replicas").json()
    yaml = detail["detail"]["yaml"]
    assert yaml["available"] is True
    assert yaml["applicable"] is False                     # offline provenance can never produce applicable YAML
    assert "topologySpreadConstraints" in yaml["text"]
    assert yaml["sha256"]
    # every per-candidate proposal is likewise inapplicable
    for candidate in detail["detail"]["candidates"]:
        assert candidate["yamlProposal"]["applicable"] is False


def test_offline_validation_compares_prediction_with_the_scripted_observation(client):
    validation = client.get("/api/dev-lab/scenarios/concentrated-replicas").json()["validation"]
    assert validation["available"] is True
    spread = validation["runs"]["schedulerSpreads"]
    stay = validation["runs"]["schedulerChangesNothing"]
    # The scripted spread scheduler places pods as predicted: agreement proves the comparison arithmetic.
    assert spread["state"] == "COMPLETED"
    assert spread["validated"] is True
    assert spread["predictionError"] == pytest.approx(0.0)
    # The scripted no-change scheduler leaves the problem in place: the comparison must report the mismatch.
    assert stay["validated"] is False
    assert stay["predictionError"] > 0
    assert stay["reasons"]
    comparison = spread["comparison"]
    assert comparison["mandatoryWorkflowSurvival"]["match"] is True
    assert comparison["blastRadius"]["predictedAfter"] == pytest.approx(comparison["blastRadius"]["observedAfter"])


def test_validation_is_not_offered_without_a_generated_proposal(client):
    # a selected, generated proposal is validated ...
    assert client.get("/api/dev-lab/scenarios/single-replica").json()["validation"]["available"] is True
    # ... and without one there is honestly nothing to validate
    assert client.get("/api/dev-lab/scenarios/insufficient-cpu").json()["validation"]["available"] is False


def test_custom_ranking_weights_are_applied_and_reported(client):
    default = client.get("/api/dev-lab/scenarios/soft-anti-affinity-hardened").json()
    custom = client.get(
        "/api/dev-lab/scenarios/soft-anti-affinity-hardened",
        params={"target_share": 0.1, "disruption_discount": 0.9, "resource_discount": 0.9},
    ).json()
    assert default["customWeights"] is None
    assert custom["customWeights"]["targetShare"] == pytest.approx(0.1)
    assert custom["ranking"]["weights"]["targetShare"] == pytest.approx(0.1)
    assert default["ranking"]["weights"]["targetShare"] == pytest.approx(0.7)


@pytest.mark.parametrize("params", [{"target_share": 2}, {"disruption_discount": -0.1}])
def test_out_of_range_weights_are_rejected(client, params):
    assert client.get("/api/dev-lab/scenarios/concentrated-replicas", params=params).status_code == 422


def test_an_unknown_scenario_is_a_404(client):
    response = client.get("/api/dev-lab/scenarios/not-a-scenario")
    assert response.status_code == 404


def test_the_lab_exposes_no_write_endpoints():
    for route in create_app().routes:
        methods = getattr(route, "methods", None)
        if methods:
            assert methods <= {"GET", "HEAD", "OPTIONS"}, f"{route} is not read-only"
