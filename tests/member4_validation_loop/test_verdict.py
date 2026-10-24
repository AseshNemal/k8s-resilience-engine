"""The pass rule: readiness and the probe judged separately, a candidate proven only with complete evidence."""

import pytest

from app.engine.recommendation_evaluation_validation.validation_loop.models import (
    BASELINE,
    CANDIDATE,
    CandidateOutcome,
    CandidateStatus,
    ExperimentKey,
    ExperimentOutcome,
    ExperimentStatus,
)
from app.engine.recommendation_evaluation_validation.validation_loop.verdict import (
    judge_candidate,
    judge_experiment,
    mandatory_readiness,
    select_winner,
)
from tests.support.member4_harness import bookinfo_scenario

KEY = ExperimentKey(CANDIDATE, "c1", "w1", 1)
MANDATORY = ["productpage", "details", "reviews"]


def placed(**moves):
    """The concentrated Bookinfo graph with some services' pods moved: {service: [nodes...]}."""
    graph = bookinfo_scenario("concentrated").deployment.model_copy(deep=True)
    for service in graph.services:
        for pod, node in zip(service.pods, moves.get(service.name, [])):
            pod.node = node
    return graph


def failed(graph, worker):
    for service in graph.services:
        for pod in service.pods:
            pod.ready = pod.ready and pod.node != worker
    return graph


def probe(survives):
    return {"workflowSurvives": survives, "windows": {"failure": {"reasons": {"status 503, expected 200": 3}}}}


def spread_graph(worker="w1"):
    return failed(placed(reviews=["w1", "w2", "w3"], ratings=["w2", "w3"]), worker)


# --- readiness ---------------------------------------------------------------------------------------------------

def test_every_mandatory_service_keeping_a_ready_pod_elsewhere_holds():
    readiness = mandatory_readiness(spread_graph(), MANDATORY, "w1")

    assert readiness["holds"] is True
    assert readiness["services"]["reviews"] == {"readyElsewhere": 2, "readyOnWorker": 0, "ok": True}


def test_a_mandatory_service_entirely_on_the_stopped_worker_does_not_hold():
    readiness = mandatory_readiness(failed(bookinfo_scenario("concentrated").deployment.model_copy(deep=True), "w1"), MANDATORY, "w1")

    assert readiness["holds"] is False and readiness["services"]["reviews"]["ok"] is False


def test_a_service_missing_from_the_observation_is_unknown_not_assumed_fine():
    readiness = mandatory_readiness(spread_graph(), [*MANDATORY, "not-observed"], "w1")

    assert readiness["holds"] is None and readiness["services"]["not-observed"]["ok"] is None


# --- one experiment ----------------------------------------------------------------------------------------------

def test_readiness_alone_passes_when_no_probe_is_configured_and_says_so():
    outcome = judge_experiment(KEY, mandatory_readiness(spread_graph(), MANDATORY, "w1"), None, probe_required=False)

    assert outcome.status is ExperimentStatus.PASS and outcome.evidence_level == "L1_READINESS"


def test_a_configured_probe_that_saw_the_workflow_working_passes_at_the_probe_level():
    outcome = judge_experiment(KEY, mandatory_readiness(spread_graph(), MANDATORY, "w1"), probe(True), probe_required=True)

    assert outcome.status is ExperimentStatus.PASS and outcome.evidence_level == "L2_PROBE"


def test_a_probe_failure_fails_the_experiment_even_when_readiness_holds():
    outcome = judge_experiment(KEY, mandatory_readiness(spread_graph(), MANDATORY, "w1"), probe(False), probe_required=True)

    assert outcome.status is ExperimentStatus.FAIL
    assert any("workflow check failed" in r and "503" in r for r in outcome.reasons)


def test_too_few_probe_samples_is_inconclusive_not_a_pass():
    outcome = judge_experiment(KEY, mandatory_readiness(spread_graph(), MANDATORY, "w1"), probe(None), probe_required=True)

    assert outcome.status is ExperimentStatus.INCONCLUSIVE and "too few samples" in " ".join(outcome.reasons)


def test_a_missing_probe_report_when_one_is_required_is_inconclusive():
    outcome = judge_experiment(KEY, mandatory_readiness(spread_graph(), MANDATORY, "w1"), None, probe_required=True)

    assert outcome.status is ExperimentStatus.INCONCLUSIVE


def test_unreadable_readiness_is_inconclusive():
    outcome = judge_experiment(KEY, mandatory_readiness(spread_graph(), [*MANDATORY, "x"], "w1"), None, probe_required=False)

    assert outcome.status is ExperimentStatus.INCONCLUSIVE


def test_a_service_with_no_ready_pod_elsewhere_fails_with_the_services_named():
    graph = failed(bookinfo_scenario("concentrated").deployment.model_copy(deep=True), "w1")

    outcome = judge_experiment(KEY, mandatory_readiness(graph, MANDATORY, "w1"), None, probe_required=False)

    assert outcome.status is ExperimentStatus.FAIL and "reviews" in outcome.reasons[0]


def comparison(match):
    return {"mandatoryWorkflowSurvival": {"match": match}, "serviceSurvival": {"services": [{"match": True}]}}


def test_a_probe_that_disagrees_with_readiness_is_its_own_finding_not_a_model_error():
    """Readiness holds, the probe fails, and the model (which predicts from readiness) agreed with readiness."""
    outcome = judge_experiment(KEY, mandatory_readiness(spread_graph(), MANDATORY, "w1"), probe(False), probe_required=True,
                               comparison=comparison(True))

    assert outcome.status is ExperimentStatus.FAIL
    assert outcome.model_agrees is True
    assert outcome.probe_disagrees_with_readiness is True


def test_the_model_is_marked_wrong_only_when_it_disagrees_with_the_readiness_derived_observation():
    outcome = judge_experiment(KEY, mandatory_readiness(spread_graph(), MANDATORY, "w1"), None, probe_required=False,
                               comparison=comparison(False))

    assert outcome.model_agrees is False and outcome.probe_disagrees_with_readiness is None


def test_service_level_disagreement_also_marks_the_model_wrong():
    mismatch = {"mandatoryWorkflowSurvival": {"match": True}, "serviceSurvival": {"services": [{"match": True}, {"match": False}]}}

    outcome = judge_experiment(KEY, mandatory_readiness(spread_graph(), MANDATORY, "w1"), None, probe_required=False, comparison=mismatch)

    assert outcome.model_agrees is False


# --- a candidate and the winner ----------------------------------------------------------------------------------

def experiment(worker, status, repeat=1, kind=CANDIDATE):
    return ExperimentOutcome(key=ExperimentKey(kind, "c1", worker, repeat), status=status)


def test_a_candidate_is_proven_only_when_every_planned_experiment_passed():
    passes = [experiment(w, ExperimentStatus.PASS) for w in ("w1", "w2", "w3")]

    assert judge_candidate(3, passes) == (CandidateStatus.PASSED, None)


def test_one_failed_experiment_fails_the_candidate_and_names_where():
    status, reason = judge_candidate(3, [experiment("w1", ExperimentStatus.PASS), experiment("w2", ExperimentStatus.FAIL)])

    assert status is CandidateStatus.FAILED and reason.startswith("w2 (repeat 1)")


def test_a_skipped_or_inconclusive_experiment_means_the_candidate_is_not_proven():
    for gap in (ExperimentStatus.SKIPPED, ExperimentStatus.INCONCLUSIVE):
        status, reason = judge_candidate(3, [experiment("w1", ExperimentStatus.PASS), experiment("w2", gap), experiment("w3", ExperimentStatus.SKIPPED)])
        assert status is CandidateStatus.INCONCLUSIVE and "did not produce a pass" in reason


def test_baseline_experiments_never_count_toward_a_candidate():
    outcomes = [experiment("w1", ExperimentStatus.FAIL, kind=BASELINE), experiment("w1", ExperimentStatus.PASS)]

    assert judge_candidate(1, outcomes)[0] is CandidateStatus.PASSED


def test_the_smallest_proven_change_wins_and_an_unproven_candidate_is_never_selected():
    outcomes = [CandidateOutcome("big", CandidateStatus.PASSED), CandidateOutcome("small", CandidateStatus.PASSED),
                CandidateOutcome("tiny-but-unproven", CandidateStatus.INCONCLUSIVE)]
    order = {"big": (3, 1), "small": (1, 2), "tiny-but-unproven": (0, 0)}

    assert select_winner(outcomes, order) == "small"
    assert select_winner([CandidateOutcome("a", CandidateStatus.INCONCLUSIVE), CandidateOutcome("b", CandidateStatus.FAILED)],
                         {"a": (1,), "b": (2,)}) is None


@pytest.mark.parametrize("status", [ExperimentStatus.PASS, ExperimentStatus.FAIL])
def test_an_outcome_serialises_with_readiness_and_probe_kept_in_separate_fields(status):
    outcome = judge_experiment(KEY, mandatory_readiness(spread_graph(), MANDATORY, "w1"), probe(status is ExperimentStatus.PASS),
                               probe_required=True)

    data = outcome.to_dict()
    assert data["readiness"]["holds"] is True and data["probe"]["workflowSurvives"] is (status is ExperimentStatus.PASS)
