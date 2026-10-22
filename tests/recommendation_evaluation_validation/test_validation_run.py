"""Post-deployment validation: apply an approved change to a TEST cluster, fail a node, compare with the prediction."""

import pytest

from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation.validation import (
    APPLY,
    FAIL_BASELINE,
    FAIL_NODE,
    RunState,
    TestTarget,
    ValidationPlan,
    ValidationRefusedError,
    ValidationRun,
    ValidationThresholds,
    authorize,
    to_validation_report,
)
from app.shared.schemas.approval import ApprovalStatus
from tests.support.member4_approvals import approved_for_test, as_live, requested_for_test
from tests.support.member4_harness import bookinfo_scenario
from tests.support.member4_test_cluster import FakeTestCluster, pack, stay

TARGET = TestTarget("kind-test", "test")


def prepare(**cluster_options):
    scenario = bookinfo_scenario("concentrated")
    approval, live = approved_for_test(scenario)
    plan = ValidationPlan.from_approval(approval, live, scenario.workflow, TARGET)
    cluster = FakeTestCluster(scenario.deployment, **cluster_options)
    return scenario, approval, live, plan, cluster


def grants(plan, who="dev"):
    return authorize(plan, APPLY, who), authorize(plan, FAIL_NODE, who)


def run_all(plan, cluster, **options):
    apply_auth, failure_auth = grants(plan)
    return ValidationRun(plan, cluster, **options).execute(apply_auth, failure_auth)


# --- the happy path: the prediction holds ------------------------------------------------------------------------

def test_a_prediction_that_holds_is_validated_with_every_figure_compared():
    _, _, _, plan, cluster = prepare()

    result = run_all(plan, cluster)

    assert result.state is RunState.COMPLETED and result.validated is True and result.reasons == ()
    c = result.comparison
    assert c["mandatoryWorkflowSurvival"]["predictedSurvives"] is True and c["mandatoryWorkflowSurvival"]["match"] is True
    assert c["serviceSurvival"]["agreement"] == "4/4"
    assert c["blastRadius"]["predictedBefore"] == pytest.approx(100.0) and c["blastRadius"]["predictedAfter"] == pytest.approx(0.0)
    assert c["blastRadius"]["observedAfter"] == pytest.approx(0.0) and c["blastRadius"]["absoluteError"] == pytest.approx(0.0)
    assert c["riskReduction"]["predicted"] == pytest.approx(100.0) and c["riskReduction"]["observed"] == pytest.approx(100.0)
    assert result.prediction_error == pytest.approx(0.0)
    assert c["placement"]["match"] is True


def test_without_a_baseline_failure_the_observed_risk_reduction_says_it_is_partly_derived():
    result = run_all(*prepare()[3:5])

    assert result.comparison["riskReduction"]["observedBasis"] == "DERIVED_FROM_PLACEMENT"
    assert "partly a prediction" in result.comparison["riskReduction"]["caveat"]


def test_a_measured_baseline_makes_the_observed_risk_reduction_a_measurement():
    _, _, _, plan, cluster = prepare()
    run = ValidationRun(plan, cluster)

    result = run.execute(*grants(plan), baseline=authorize(plan, FAIL_BASELINE, "dev"))

    assert result.comparison["riskReduction"]["observedBasis"] == "MEASURED"
    assert "caveat" not in result.comparison["riskReduction"]
    assert result.observed["blastRadiusBefore"] == pytest.approx(100.0)


# --- the prediction does not hold ---------------------------------------------------------------------------------

def test_a_scheduler_that_changes_nothing_is_a_failed_prediction_with_its_error_measured():
    _, _, _, plan, cluster = prepare(scheduler=stay)

    result = run_all(plan, cluster)

    assert result.state is RunState.COMPLETED and result.validated is False
    assert result.prediction_error == pytest.approx(100.0)
    assert result.comparison["mandatoryWorkflowSurvival"] == {
        "workflowId": "productpage-view", "predictedSurvives": True, "observedSurvives": False, "match": False,
        "mandatoryServices": ["productpage", "details", "reviews"]}
    assert result.comparison["riskReduction"]["absoluteError"] == pytest.approx(100.0)
    assert result.comparison["serviceSurvival"]["falseNegatives"] == ["reviews"]
    assert any("predicted to survive" in reason for reason in result.reasons)


def test_a_different_placement_that_still_survives_is_reported_as_a_placement_mismatch_not_hidden():
    _, _, _, plan, cluster = prepare(scheduler=pack("w2"))      # everything lands on w2; w1 is the failed node

    result = run_all(plan, cluster)

    assert result.comparison["placement"]["match"] is False
    assert result.comparison["blastRadius"]["observedAfter"] == pytest.approx(0.0)
    assert result.validated is True      # the outcome was predicted correctly, even though the path differed


def test_thresholds_are_configurable():
    _, _, _, plan, cluster = prepare(scheduler=stay)
    loose = ValidationThresholds(blast_radius_tolerance=100.0, risk_reduction_tolerance=100.0,
                                 require_service_match=False, require_workflow_match=False)

    result = run_all(plan, cluster, thresholds=loose)

    assert result.validated is True      # nothing in the thresholds objected; the error is still reported
    assert result.prediction_error == pytest.approx(100.0)


def test_the_prediction_is_fixed_before_the_run_and_unchanged_by_what_is_observed():
    _, _, _, plan, cluster = prepare(scheduler=stay)
    before = (plan.predicted.after.model_dump(), plan.predicted.before.model_dump(), dict(plan.predicted.placement))

    result = run_all(plan, cluster)

    assert (plan.predicted.after.model_dump(), plan.predicted.before.model_dump(), dict(plan.predicted.placement)) == before
    assert result.predicted["blastRadiusAfter"] == pytest.approx(0.0) and result.observed["blastRadiusAfter"] == pytest.approx(100.0)


# --- the shared contract -----------------------------------------------------------------------------------------

def test_a_completed_run_reduces_to_the_shared_validation_report():
    _, _, _, plan, cluster = prepare(scheduler=stay)

    report = to_validation_report(run_all(plan, cluster))

    assert (report.recommendation_id, report.predicted_blast_radius, report.observed_blast_radius) == (
        plan.recommendation_id, pytest.approx(0.0), pytest.approx(100.0))
    assert report.prediction_error == pytest.approx(100.0) and report.validated is False


def test_the_observed_side_agrees_with_component_3_on_the_same_placement():
    scenario, _, _, plan, cluster = prepare()
    result = run_all(plan, cluster)

    direct = predict_failure_impact(scenario.workflow, cluster.graph, plan.predicted.failed_node)

    assert result.observed["blastRadiusAfter"] == direct.blast_radius
