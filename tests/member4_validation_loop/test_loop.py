"""The loop over test doubles: apply once, workers in turn, recovery verified, early stop, skipped recorded, always reverted."""

from datetime import datetime, timezone

import pytest

from app.engine.recommendation_evaluation_validation.validation import TestTarget, predict_outcome
from app.engine.recommendation_evaluation_validation.validation_loop.grant import (
    GrantAuthority,
    GrantedCandidate,
    InMemoryKillBudget,
    new_grant,
)
from app.engine.recommendation_evaluation_validation.validation_loop.loop import (
    KillBlocked,
    LoopCandidate,
    LoopObserver,
    ValidationLoop,
    observed_estimate,
)
from app.engine.recommendation_evaluation_validation.validation_loop.models import (
    CandidateStatus,
    ExperimentStatus,
    Mode,
    RunStatus,
    resolve_mode,
)
from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from tests.support.member4_approvals import as_live
from tests.support.member4_harness import bookinfo_scenario
from tests.support.member4_test_cluster import FakeTestCluster, stay

WORKERS = ["w1", "w2", "w3"]


class ProbingCluster(FakeTestCluster):
    """A fake test cluster whose workflow check says what the test scripts it to say."""

    def __init__(self, *args, probe_says=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.probe_says = probe_says

    def probe_reports(self):
        return {"afterChange": {"workflowSurvives": self.probe_says, "windows": {"failure": {"reasons": {}}}},
                "baselineFailure": {"workflowSurvives": self.probe_says, "windows": {"failure": {"reasons": {}}}}}


class Recorder(LoopObserver):
    def __init__(self):
        self.events = []

    def run_started(self, plan, candidates):
        self.events.append(("run", tuple(candidates)))

    def experiment_planned(self, key):
        self.events.append(("planned", key.worker, key.repeat))

    def stage(self, key, stage, detail=""):
        self.events.append(("stage", stage))

    def experiment_finished(self, outcome):
        self.events.append(("finished", outcome.key.worker, outcome.status.value))

    def candidate_finished(self, outcome):
        self.events.append(("candidate", outcome.candidate_id, outcome.status.value))


def setup(mode=Mode.QUICK, *, cluster_class=FakeTestCluster, cluster_options=None, max_failures=None, **loop_options):
    scenario = bookinfo_scenario("concentrated")
    live = as_live(scenario.evaluate())
    recommendation_id = live.report.selected_recommendation_id
    proposal = build_yaml_proposal(live, recommendation_id)
    plan = resolve_mode(mode, WORKERS, riskiest_worker="w1")
    predicted = {w: predict_outcome(scenario.workflow, live, recommendation_id, failed_node=w) for w in WORKERS}
    candidate = LoopCandidate(recommendation_id, proposal.yaml, proposal.sha256, predicted, (1, 1))
    cluster = cluster_class(scenario.deployment, **(cluster_options or {}))
    grant = new_grant(run_id="run-1", plan_digest_value="p" * 64, target_name=cluster.target.name, cluster_uid="uid-1",
                      namespace="bookinfo", workers=plan.workers, candidates=(GrantedCandidate(recommendation_id, proposal.sha256),),
                      repeats=plan.repeats, measure_baseline=plan.measure_baseline,
                      approved_at=datetime(2026, 10, 24, tzinfo=timezone.utc))
    if max_failures is not None:
        grant = type(grant)(**{**grant.__dict__, "max_failures": max_failures})
    budget = InMemoryKillBudget()
    armed = []
    options = dict(workflow=scenario.workflow, plan=plan, authority=GrantAuthority(grant, budget), candidates=[candidate],
                   executor_factory=lambda _candidate: cluster, arm=lambda auth, worker, digest: armed.append((worker, auth.action)))
    options.update(loop_options)
    return ValidationLoop(**options), cluster, budget, armed, candidate


def calls(cluster, prefix):
    return [c for c in cluster.calls if c.startswith(prefix)]


# --- the happy path ----------------------------------------------------------------------------------------------

def test_quick_proves_the_top_candidate_on_every_worker_applying_it_once_and_reverting_it():
    loop, cluster, budget, armed, candidate = setup()

    result = loop.run()

    assert result.status is RunStatus.COMPLETED and result.winner == candidate.candidate_id
    assert [e.status for e in result.candidates[0].experiments] == [ExperimentStatus.PASS] * 3
    assert len(calls(cluster, "apply")) == 1                                 # applied ONCE per candidate
    assert calls(cluster, "fail:") == ["fail:w1", "fail:w2", "fail:w3"]      # workers in turn
    assert calls(cluster, "restore:") == ["restore:w1", "restore:w2", "restore:w3"]
    assert cluster.calls.index("revert") > cluster.calls.index("restore:w3")
    assert result.candidates[0].reverted is True and budget.used("run-1") == 3 == result.kills_used
    assert [worker for worker, _ in armed] == WORKERS and {action for _, action in armed} == {"STOP_KIND_WORKER_CONTAINER"}


def test_each_failure_is_followed_by_its_recovery_before_the_next_failure():
    loop, cluster, *_ = setup()

    loop.run()

    order = [c for c in cluster.calls if c.startswith(("fail:", "restore:"))]
    assert order == ["fail:w1", "restore:w1", "fail:w2", "restore:w2", "fail:w3", "restore:w3"]


def test_every_experiment_records_its_recovery_and_the_stage_order_the_page_shows():
    recorder = Recorder()
    loop, *_ = setup(observer=recorder)

    result = loop.run()

    assert all(e.recovery == {"worker": e.key.worker, "restored": True, "verified": True, "independentCheck": None}
               for e in result.candidates[0].experiments)
    stages = [e[1] for e in recorder.events if e[0] == "stage"]
    assert stages[:3] == ["PREPARE", "APPLY_CANDIDATE", "VERIFY_ROLLOUT"] and stages[-1] == "RESTORE_DEPLOYMENT"
    assert stages.count("INJECT_FAILURE") == stages.count("RECOVER_WORKER") == 3


def test_smoke_stops_one_worker_once():
    loop, cluster, budget, *_ = setup(Mode.SMOKE)

    result = loop.run()

    assert calls(cluster, "fail:") == ["fail:w1"] and budget.used("run-1") == 1 and result.status is RunStatus.COMPLETED


def test_full_measures_a_baseline_on_the_unchanged_deployment_before_the_candidate_and_repeats_workers():
    loop, cluster, budget, *_ = setup(Mode.FULL)

    result = loop.run()

    assert [e.key.worker for e in result.baseline] == WORKERS
    assert cluster.calls.index("apply") > cluster.calls.index("restore:w3")      # baseline failures happened first
    assert len(calls(cluster, "fail:")) == 3 + 3 * 2 and budget.used("run-1") == 9
    assert [e.key.repeat for e in result.candidates[0].experiments] == [1, 1, 1, 2, 2, 2]
    assert result.candidates[0].experiments[0].comparison["blastRadius"]["baselineBasis"] == "MEASURED"


def test_without_a_measured_baseline_the_comparison_says_it_is_derived_from_placement():
    loop, *_ = setup(Mode.QUICK)

    result = loop.run()

    assert result.candidates[0].experiments[0].comparison["blastRadius"]["baselineBasis"] == "DERIVED_FROM_PLACEMENT"


def test_the_prediction_agrees_with_what_was_observed_when_the_scheduler_spreads():
    loop, *_ = setup()

    experiments = loop.run().candidates[0].experiments

    assert all(e.model_agrees is True for e in experiments)
    assert all(e.prediction_error == pytest.approx(0.0) for e in experiments)


# --- early termination and skipped experiments ---------------------------------------------------------------------

def test_a_conclusive_failure_stops_the_candidate_and_records_the_rest_as_skipped():
    loop, cluster, budget, *_ = setup(cluster_options={"scheduler": stay})

    result = loop.run()

    candidate = result.candidates[0]
    assert candidate.status is CandidateStatus.FAILED and result.winner is None
    assert [e.status for e in candidate.experiments] == [ExperimentStatus.FAIL, ExperimentStatus.SKIPPED, ExperimentStatus.SKIPPED]
    assert "Stopped after w1" in candidate.experiments[1].reasons[0]
    assert calls(cluster, "fail:") == ["fail:w1"] and budget.used("run-1") == 1       # the skipped ones were never killed
    assert candidate.reverted is True


def test_a_model_mismatch_is_flagged_even_though_the_verdict_follows_the_observation():
    loop, *_ = setup(cluster_options={"scheduler": stay})

    first = loop.run().candidates[0].experiments[0]

    assert first.status is ExperimentStatus.FAIL and first.model_agrees is False


def test_a_rollout_that_does_not_settle_skips_every_experiment_and_still_reverts():
    loop, cluster, budget, *_ = setup(cluster_options={"settles": False})

    result = loop.run()

    assert result.candidates[0].status is CandidateStatus.INCONCLUSIVE and result.winner is None
    assert {e.status for e in result.candidates[0].experiments} == {ExperimentStatus.SKIPPED}
    assert calls(cluster, "fail:") == [] and "revert" in cluster.calls and budget.used("run-1") == 0


# --- separate evidence: the probe ---------------------------------------------------------------------------------

def test_a_probe_failure_fails_the_candidate_but_is_not_called_a_model_error():
    loop, *_ = setup(cluster_class=ProbingCluster, cluster_options={"probe_says": False}, probe_required=True)

    first = loop.run().candidates[0].experiments[0]

    assert first.status is ExperimentStatus.FAIL
    assert first.model_agrees is True and first.probe_disagrees_with_readiness is True
    assert first.readiness["holds"] is True and first.probe["workflowSurvives"] is False


def test_a_passing_probe_and_readiness_pass_at_the_probe_evidence_level():
    loop, *_ = setup(cluster_class=ProbingCluster, probe_required=True)

    result = loop.run()

    assert result.winner is not None and result.candidates[0].experiments[0].evidence_level == "L2_PROBE"


def test_a_required_probe_with_no_report_is_inconclusive_and_the_candidate_is_never_selected():
    loop, *_ = setup(probe_required=True)       # the plain fake has no probe_reports

    result = loop.run()

    assert result.candidates[0].status is CandidateStatus.INCONCLUSIVE and result.winner is None


# --- safety: blocks, budget, abort, restore failures ---------------------------------------------------------------

def test_a_blocked_kill_is_never_performed_and_the_candidate_is_not_proven():
    def block(worker, candidate_id):
        raise KillBlocked("CRITICAL_WORKLOAD", "coredns would have no ready replica")

    loop, cluster, budget, *_ = setup(before_kill=block)

    result = loop.run()

    assert calls(cluster, "fail:") == [] and budget.used("run-1") == 0
    assert result.candidates[0].experiments[0].status is ExperimentStatus.INCONCLUSIVE
    assert "CRITICAL_WORKLOAD" in result.candidates[0].experiments[0].reasons[0] and result.winner is None


def test_a_kill_check_that_cannot_complete_refuses_the_kill_it_fails_closed():
    def broken(worker, candidate_id):
        raise RuntimeError("could not read the pods on the worker")

    loop, cluster, *_ = setup(before_kill=broken)

    result = loop.run()

    assert calls(cluster, "fail:") == [] and "KILL_CHECK_FAILED" in result.candidates[0].experiments[0].reasons[0]


def test_the_kill_budget_stops_the_whole_run_before_the_extra_failure_and_reverts():
    loop, cluster, budget, *_ = setup(max_failures=2)

    result = loop.run()

    assert result.status is RunStatus.INTERRUPTED and "GRANT_KILL_BUDGET_EXHAUSTED" in result.stop_reason
    assert len(calls(cluster, "fail:")) == 2 and budget.used("run-1") == 2
    assert result.candidates[0].reverted is True and result.winner is None
    assert result.candidates[0].experiments[-1].status is ExperimentStatus.SKIPPED


def test_abort_stops_at_a_safe_point_recovers_reverts_and_records_the_rest_as_skipped():
    state = {"asked": 0}

    def abort():
        state["asked"] += 1
        return state["asked"] > 2      # allow the candidate to start and the first worker, then stop

    loop, cluster, budget, *_ = setup(abort=abort)

    result = loop.run()

    assert result.status is RunStatus.INTERRUPTED and "Abort was requested" in result.stop_reason
    assert calls(cluster, "fail:") == ["fail:w1"] and calls(cluster, "restore:") == ["restore:w1"]
    assert "revert" in cluster.calls and result.candidates[0].reverted is True and result.winner is None
    assert [e.status for e in result.candidates[0].experiments][1:] == [ExperimentStatus.SKIPPED] * 2


def test_a_worker_that_cannot_be_restored_stops_the_run_flags_it_and_still_reverts():
    loop, cluster, *_ = setup(cluster_options={"restore_fails": True})

    result = loop.run()

    assert result.status is RunStatus.NEEDS_MANUAL_RESTORE and result.needs_manual_restore is True
    assert calls(cluster, "fail:") == ["fail:w1"]                    # no further worker is stopped
    assert "revert" in cluster.calls and result.winner is None


def test_a_deployment_that_cannot_be_put_back_is_flagged_and_ends_the_run():
    loop, *_ = setup(cluster_options={"revert_fails": True})

    result = loop.run()

    assert result.status is RunStatus.NEEDS_MANUAL_RESTORE and result.candidates[0].reverted is False
    assert result.winner is None


def test_a_worker_reported_not_ready_after_restore_is_flagged_even_when_the_start_succeeded():
    loop, *_ = setup(node_ready=lambda worker: False)

    result = loop.run()

    assert result.status is RunStatus.NEEDS_MANUAL_RESTORE
    assert "does not report Ready" in result.candidates[0].experiments[0].recovery["error"]


def test_a_failure_that_was_not_produced_is_inconclusive_and_the_worker_is_still_restored():
    loop, cluster, *_ = setup(cluster_options={"fail_at": "failure"})

    result = loop.run()

    assert result.candidates[0].experiments[0].status is ExperimentStatus.INCONCLUSIVE
    assert calls(cluster, "restore:") == ["restore:w1"] and result.candidates[0].reverted is True


def test_a_run_never_writes_to_a_target_that_is_not_a_test_environment():
    loop, cluster, *_ = setup(cluster_options={"environment": "production"})

    result = loop.run()

    assert result.status is RunStatus.INTERRUPTED and "GRANT_TARGET_MISMATCH" in result.stop_reason
    assert cluster.wrote is False and calls(cluster, "apply") == [] and calls(cluster, "fail:") == [] and result.winner is None


# --- estimates ---------------------------------------------------------------------------------------------------

def test_an_estimate_exists_only_from_experiments_that_actually_ran():
    assert observed_estimate([], 5) is None

    estimate = observed_estimate([60.0, 120.0], 4)
    assert estimate["estimatedSeconds"] == pytest.approx(360.0) and estimate["basedOnExperiments"] == 2
    assert estimate["minExperimentSeconds"] == pytest.approx(60.0) and estimate["maxExperimentSeconds"] == pytest.approx(120.0)


def test_experiment_durations_are_measured_with_the_injected_clock():
    ticks = iter(range(0, 1000, 7))
    loop, *_ = setup(clock=lambda: float(next(ticks)))

    experiments = loop.run().candidates[0].experiments

    assert all(e.duration_s is not None and e.duration_s > 0 for e in experiments)
