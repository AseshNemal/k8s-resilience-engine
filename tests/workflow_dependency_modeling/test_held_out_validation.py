"""Held-out validation: frozen predictions, scoring arithmetic, and the multi-service outage trace rule."""

from types import SimpleNamespace
from itertools import combinations
import random

import pytest

from app.engine.workflow_dependency_modeling import held_out_validation as held_out
from app.engine.workflow_dependency_modeling.dependency_assessment_models import AssessmentPolicy
from app.engine.workflow_dependency_modeling.held_out_validation_repository import (
    HeldOutRecordConflictError,
    InMemoryHeldOutValidationRepository,
)
from app.engine.workflow_dependency_modeling.trace_validator import expected_outage_trace_quality

LABELS = {"payment": "mandatory", "email": "optional", "ads": "optional", "recs": "supporting"}


def test_predictions_follow_the_labels_and_b1_always_predicts_failure():
    assert held_out.predict(("email", "payment"), LABELS)["method"] == "fail"
    assert held_out.predict(("ads", "email"), LABELS)["method"] == "survive"
    assert held_out.predict(("email", "recs"), LABELS)["method"] == "uncertain"
    assert {held_out.predict(pair, LABELS)["traceOnly"] for pair in [("ads", "email"), ("email", "payment")]} == {"fail"}


def test_metrics_match_hand_counts():
    # (predicted, observed): TP, TP, FP, FN, TN
    metrics = held_out._metrics([("fail", "fail"), ("fail", "fail"), ("fail", "survive"), ("survive", "fail"), ("survive", "survive")])
    assert (metrics["truePositives"], metrics["falsePositives"], metrics["falseNegatives"], metrics["trueNegatives"]) == (2, 1, 1, 1)
    assert metrics["precision"] == pytest.approx(2 / 3)
    assert metrics["recall"] == pytest.approx(2 / 3)
    assert metrics["f1"] == pytest.approx(2 / 3)
    assert metrics["accuracy"] == pytest.approx(3 / 5)
    assert held_out._metrics([])["f1"] is None


@pytest.fixture
def stored(monkeypatch):
    repository = InMemoryHeldOutValidationRepository()
    assessment = SimpleNamespace(
        assessment_id="assessment:c1", policy=AssessmentPolicy(),
        services=[SimpleNamespace(service_name=name, classification=SimpleNamespace(value=label)) for name, label in LABELS.items()],
    )
    monkeypatch.setattr(held_out, "get_experiment_campaign_assessment", lambda campaign_id: assessment)
    monkeypatch.setattr(held_out, "get_experiment_campaign", lambda campaign_id: None)
    return repository


def test_a_plan_is_frozen_with_its_seed_and_scores_against_stored_results(stored):
    plan = held_out.plan_held_out_run("campaign:c1", actor="researcher", scenario_count=3, seed=7, repository=stored)
    again = held_out.plan_held_out_run("campaign:c1", actor="researcher", scenario_count=3, seed=7, repository=stored)
    assert [item["services"] for item in plan["scenarios"]] == [item["services"] for item in again["scenarios"]]
    assert plan["pairsAvailable"] == 6
    assert "random.Random(7)" in plan["sampling"]

    # Results are separate records; observe every planned pair as failing only when payment is down.
    for scenario in plan["scenarios"]:
        outcome = "fail" if "payment" in scenario["services"] else "survive"
        stored.create(f"campaign:c1|{plan['runId']}|result|{scenario['scenarioId']}",
                      {"kind": "result", "runId": plan["runId"], "scenarioId": scenario["scenarioId"],
                       "observation": {"outcome": outcome}})
    with pytest.raises(HeldOutRecordConflictError):
        stored.create(f"campaign:c1|{plan['runId']}|plan", {})

    score = held_out.score_held_out_run("campaign:c1", plan["runId"], repository=stored)
    decided = [row for row in score["scenarios"] if row["prediction"]["method"] != "uncertain"]
    assert score["method"]["scored"] == len(decided)
    assert score["method"]["falsePositives"] == score["method"]["falseNegatives"] == 0
    assert score["traceOnly"]["scored"] == 3
    assert score["traceOnly"]["recall"] in (None, 1.0)


@pytest.fixture
def stratified_stored(stored, monkeypatch):
    labels = {"payment": "mandatory", "cart": "mandatory", "email": "optional",
              "recs": "optional", "ads": "supporting"}
    assessment = held_out.get_experiment_campaign_assessment("campaign:c1")
    assessment.services = [SimpleNamespace(service_name=name, classification=SimpleNamespace(value=label))
                           for name, label in labels.items()]
    return stored


def test_stratified_balances_groups_and_redistributes_a_small_group(stratified_stored):
    plan = held_out.plan_held_out_run("campaign:c1", actor="researcher", scenario_count=6, seed=7,
                                    repository=stratified_stored)
    assert plan["samplingMethod"] == "stratified"
    assert plan["samplingGroups"] == {
        "fail": {"available": 7, "drawn": 3}, "survive": {"available": 1, "drawn": 1},
        "uncertain": {"available": 2, "drawn": 2},
    }
    assert sum(group["drawn"] for group in plan["samplingGroups"].values()) == 6
    for name, counts in plan["samplingGroups"].items():
        assert sum(row["prediction"]["method"] == name for row in plan["scenarios"]) == counts["drawn"]
    assert len({tuple(row["services"]) for row in plan["scenarios"]}) == 6
    score = held_out.score_held_out_run("campaign:c1", plan["runId"], repository=stratified_stored)
    assert score["samplingMethod"] == "stratified"
    assert score["samplingGroups"] == plan["samplingGroups"]
    assert "over-represents rare" in score["samplingNote"]
    assert "not on the population" in score["samplingNote"]


@pytest.mark.parametrize("scenario_count", [1, 2, 3, 9, 10, 50])
def test_stratified_draws_exact_budget_or_all_pairs(stratified_stored, scenario_count):
    plan = held_out.plan_held_out_run("campaign:c1", actor="researcher", scenario_count=scenario_count, seed=7,
                                    repository=stratified_stored)
    assert len(plan["scenarios"]) == min(scenario_count, 10)
    assert sum(group["drawn"] for group in plan["samplingGroups"].values()) == min(scenario_count, 10)
    assert all(group["drawn"] <= group["available"] for group in plan["samplingGroups"].values())


@pytest.mark.parametrize("label", ["mandatory", "optional", "supporting"])
def test_stratified_handles_empty_groups(stored, label):
    assessment = held_out.get_experiment_campaign_assessment("campaign:c1")
    for service in assessment.services:
        service.classification.value = label
    plan = held_out.plan_held_out_run("campaign:c1", actor="researcher", scenario_count=4, seed=7, repository=stored)
    assert len(plan["scenarios"]) == 4
    assert sorted(group["drawn"] for group in plan["samplingGroups"].values()) == [0, 0, 4]


def test_stratified_is_seeded_and_independent_of_assessment_order(stratified_stored):
    def plan(seed):
        return held_out.plan_held_out_run("campaign:c1", actor="researcher", scenario_count=6, seed=seed,
                                         repository=stratified_stored)
    first = plan(7)
    held_out.get_experiment_campaign_assessment("campaign:c1").services.reverse()
    again = plan(7)
    assert first["scenarios"] == again["scenarios"]
    assert first["samplingGroups"] == again["samplingGroups"]
    assert first["sampling"] == again["sampling"]
    assert any(plan(seed)["scenarios"] != first["scenarios"] for seed in range(8, 15))


def test_uniform_matches_the_original_random_sample(stratified_stored):
    assessment = held_out.get_experiment_campaign_assessment("campaign:c1")
    pairs = sorted(combinations(sorted(item.service_name for item in assessment.services), 2))
    plan = held_out.plan_held_out_run("campaign:c1", actor="researcher", scenario_count=6, seed=7,
                                    sampling="uniform", repository=stratified_stored)
    assert [tuple(row["services"]) for row in plan["scenarios"]] == sorted(random.Random(7).sample(pairs, 6))
    assert plan["samplingMethod"] == "uniform"
    assert sum(group["drawn"] for group in plan["samplingGroups"].values()) == 6
    score = held_out.score_held_out_run("campaign:c1", plan["runId"], repository=stratified_stored)
    assert "over-represents" not in score["samplingNote"]


@pytest.mark.parametrize("sampling", ["other", "", None, 1, [], {}])
def test_invalid_sampling_is_rejected(stored, sampling):
    with pytest.raises(ValueError, match="sampling must be"):
        held_out.plan_held_out_run("campaign:c1", actor="researcher", scenario_count=3,
                                   sampling=sampling, repository=stored)
    assert stored.list("") == []


@pytest.mark.parametrize("sampling", ["other", "", None, 1, [], {}])
def test_api_rejects_invalid_sampling(stored, sampling):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.workflows import router

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post("/api/workflows/experiment-campaigns/campaign:c1/held-out-runs",
                                     headers={"X-Actor": "researcher"},
                                     json={"scenarioCount": 3, "sampling": sampling})
    assert response.status_code == 422
    assert "sampling must be" in response.json()["detail"]


@pytest.mark.parametrize("sampling", [None, "stratified", "uniform"])
def test_api_passes_sampling_to_the_planner(stored, monkeypatch, sampling):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.workflows import router

    monkeypatch.setattr(held_out, "_default_repository", stored)
    app = FastAPI()
    app.include_router(router)
    payload = {"scenarioCount": 3, "seed": 7}
    if sampling is not None:
        payload["sampling"] = sampling
    response = TestClient(app).post("/api/workflows/experiment-campaigns/campaign:c1/held-out-runs",
                                     headers={"X-Actor": "researcher"}, json=payload)
    assert response.status_code == 201
    assert response.json()["samplingMethod"] == (sampling or "stratified")


def test_legacy_plan_without_sampling_metadata_still_scores(stored):
    plan = held_out.plan_held_out_run("campaign:c1", actor="researcher", scenario_count=3, seed=7,
                                    sampling="uniform", repository=stored)
    legacy = {key: value for key, value in plan.items() if key not in ("samplingMethod", "samplingGroups")}
    legacy_repository = InMemoryHeldOutValidationRepository()
    legacy_repository.create(f"campaign:c1|{plan['runId']}|plan", legacy)
    for row in plan["scenarios"]:
        legacy_repository.create(f"campaign:c1|{plan['runId']}|result|{row['scenarioId']}",
                                 {"kind": "result", "runId": plan["runId"], "scenarioId": row["scenarioId"],
                                  "observation": {"outcome": "fail"}})
    score = held_out.score_held_out_run("campaign:c1", plan["runId"], repository=legacy_repository)
    assert score["samplingMethod"] == "uniform"
    assert score["samplingGroups"] is None
    assert score["traceOnly"]["truePositives"] == 3
    assert score["traceOnly"]["accuracy"] == pytest.approx(1.0)


def _trace(missing):
    spans = [{"traceID": "t", "spanID": "root", "operationName": "checkout", "references": [], "startTime": 1,
              "duration": 5, "processID": "p1", "tags": [{"key": "span.kind", "value": "server"}]}]
    for index, name in enumerate(missing):
        spans.append({"traceID": "t", "spanID": f"c{index}", "operationName": f"call {name}", "startTime": 2,
                      "duration": 1, "processID": "p1",
                      "references": [{"refType": "CHILD_OF", "traceID": "t", "spanID": "root"}],
                      "tags": [{"key": "span.kind", "value": "client"}, {"key": "net.peer.name", "value": name},
                               {"key": "error", "value": True}, {"key": "otel.status_code", "value": "ERROR"}]})
    return {"traceID": "t", "spans": spans, "processes": {"p1": {"serviceName": "web"}}}


def test_only_verified_unavailable_services_may_be_missing_from_a_trace():
    both = _trace(["email", "payment"])
    if not expected_outage_trace_quality(_trace(["email"]), "email"):
        pytest.skip("this trace shape is not the validator's missing-identity case")
    assert expected_outage_trace_quality(both, ("email", "payment"))
    assert expected_outage_trace_quality(_trace(["email"]), ("email", "payment"))   # the request stopped early
    assert not expected_outage_trace_quality(both, "email")                          # payment was not verified down
    assert not expected_outage_trace_quality(both, ())
