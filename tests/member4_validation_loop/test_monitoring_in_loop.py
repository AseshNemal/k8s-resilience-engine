"""The measured timeline inside the loop: marks in order, a monitor that fails never changes a verdict, the view and the stored record."""

from datetime import datetime, timezone

import pytest

from app.engine.recommendation_evaluation_validation.monitoring.timeline import compact
from app.engine.recommendation_evaluation_validation.validation_loop.models import Mode, ExperimentKey, ExperimentOutcome, ExperimentStatus, RunStatus
from app.engine.recommendation_evaluation_validation.validation_loop.view import build_view
from app.engine.recommendation_evaluation_validation.validation_loop.worker import DbObserver
from tests.member4_validation_loop.support import setup
from tests.member4_validation_loop.test_view import NOW, PLAN, run


def timeline_with(*, down=False):
    """A finished timeline of one failure: reviews 3 -> 2 and ratings 1 -> 0 (or stays up), requests failing for a while."""
    samples = []
    for t in range(0, 20):
        hit = 5 <= t < 12 and down
        ok = not (6 <= t < 10 and down)
        samples.append({"t": float(t), "ready": {"ratings": 0 if hit else 1, "reviews": 2 if 5 <= t < 12 else 3},
                        "nodes": {"w1": not (6 <= t < 12), "w2": True},
                        "req": {"ok": ok, "ms": 40 if ok else None, "status": 200 if ok else 503, "why": None if ok else "HTTP 503"}})
    return {"schema": "failure-timeline/v1", "evidence": "MEASURED", "startedAt": "2026-10-24T12:00:00+00:00", "intervalS": 1.0,
            "baseline": {"ratings": 1, "reviews": 3}, "samples": samples,
            "marks": [{"t": 5.0, "kind": "stop", "detail": "w1"}, {"t": 9.0, "kind": "restore", "detail": "w1"}, {"t": 12.0, "kind": "node-ready", "detail": "w1"}],
            "events": [], "logs": [], "reads": {}, "sources": {}, "truncated": False}


class ScriptedMonitor:
    def __init__(self, log, key, on_update, fail_on=None):
        self.log, self.key, self.on_update, self.fail_on = log, key, on_update, fail_on

    def begin(self):
        if self.fail_on == "begin":
            raise RuntimeError("cannot start")
        self.log.append(("begin", self.key.worker))

    def mark(self, kind, detail=""):
        if self.fail_on == "mark":
            raise RuntimeError("cannot mark")
        self.log.append((kind, detail))

    def finish(self):
        if self.fail_on == "finish":
            raise RuntimeError("cannot finish")
        self.log.append(("finish", self.key.worker))
        return timeline_with()


def monitor_for(log, fail_on=None):
    return lambda key, on_update: ScriptedMonitor(log, key, on_update, fail_on)


def test_each_failure_is_marked_stop_then_restore_then_node_ready_and_its_timeline_is_kept():
    log = []
    loop, cluster, *_ = setup(monitor_factory=monitor_for(log))

    result = loop.run()

    assert [e[0] for e in log if e[0] in ("stop", "restore", "node-ready", "begin", "finish")][:5] == ["begin", "stop", "restore", "node-ready", "finish"]
    assert all(e.timeline and e.timeline["evidence"] == "MEASURED" for e in result.candidates[0].experiments)
    assert result.status is RunStatus.COMPLETED


@pytest.mark.parametrize("fail_on", ["begin", "mark", "finish"])
def test_a_monitor_that_fails_never_changes_the_result_or_what_is_done_to_the_cluster(fail_on):
    plain_loop, plain_cluster, *_ = setup()
    plain = plain_loop.run()
    loop, cluster, *_ = setup(monitor_factory=monitor_for([], fail_on))

    result = loop.run()

    assert result.status is plain.status and result.winner == plain.winner
    assert [e.status for e in result.candidates[0].experiments] == [e.status for e in plain.candidates[0].experiments]
    assert cluster.calls == plain_cluster.calls                      # the same writes, in the same order
    if fail_on != "mark":      # a mark that cannot be written is dropped quietly; a monitor that cannot start or finish is logged
        assert any(event["event"].startswith("MONITOR_") for event in result.events)


def test_a_loop_without_a_monitor_records_no_timeline():
    loop, *_ = setup()

    result = loop.run()

    assert all(e.timeline is None for e in result.candidates[0].experiments)


def test_the_experiment_predictions_name_the_services_the_model_expected_to_lose():
    loop, *_ = setup(monitor_factory=monitor_for([]))

    outcome = loop.run().candidates[0].experiments[0]

    assert {"servicesDown", "servicesDownBefore", "workflowServices"} <= set(outcome.predicted)


# --- the view --------------------------------------------------------------------------------------------------------

def experiment(kind, worker, timeline, *, status="PASS", candidate="c1", predicted=None):
    return {"kind": kind, "candidateId": candidate if kind == "candidate" else None, "worker": worker, "repeat": 1, "status": status,
            "durationS": 60.0, "outcome": {"timeline": timeline, "predicted": predicted or {}}}


def test_the_view_summarises_each_recorded_episode_without_returning_the_samples():
    experiments = [experiment("candidate", "w1", timeline_with(), predicted={"servicesDown": [], "workflowServices": ["ratings", "reviews"], "workflowSurvives": True})]

    view = build_view(run("COMPLETED"), experiments, [], now=NOW, worker_alive=False)

    episode = view["monitor"]["episodes"][0]
    assert episode["summary"]["evidence"] == "MEASURED" and "samples" not in episode and episode["worker"] == "w1"
    assert episode["predictionCheck"]["matches"] is True and episode["predictionCheck"]["wentDownButNotPredicted"] == []
    assert view["monitor"]["comparisons"][0]["measuredBefore"] is False        # no baseline was measured: unknown, never "better"


def test_a_measured_baseline_gives_a_before_and_after_for_the_same_worker():
    experiments = [experiment("baseline", "w1", timeline_with(down=True)), experiment("candidate", "w1", timeline_with(down=False))]

    view = build_view(run("COMPLETED"), experiments, [], now=NOW, worker_alive=False)

    comparison = view["monitor"]["comparisons"][0]
    assert comparison["measuredBefore"] and comparison["improved"] is True
    by_id = {row["id"]: row for row in comparison["rows"]}
    assert by_id["servicesDown"]["before"] == 1 and by_id["servicesDown"]["after"] == 0


def test_a_prediction_the_timeline_contradicts_is_reported_as_a_difference_not_hidden():
    experiments = [experiment("candidate", "w1", timeline_with(down=True),
                              predicted={"servicesDown": [], "workflowServices": ["ratings", "reviews"], "workflowSurvives": True})]

    check = build_view(run("COMPLETED"), experiments, [], now=NOW, worker_alive=False)["monitor"]["episodes"][0]["predictionCheck"]

    assert check["matches"] is False and check["wentDownButNotPredicted"] == ["ratings"] and check["requestsFailed"] == 4


def test_the_live_timeline_shows_only_while_the_run_is_going():
    current = {"stage": "INJECT_FAILURE", "kind": "candidate", "candidateId": "c1", "worker": "w1", "repeat": 1, "timeline": timeline_with()}

    live = build_view(run(current=current), [], [], now=NOW, worker_alive=True)["monitor"]["live"]
    done = build_view(run("COMPLETED", current=current), [], [], now=NOW, worker_alive=False)["monitor"]["live"]

    assert live["worker"] == "w1" and live["summary"]["evidence"] == "MEASURED" and done is None


def test_the_research_view_carries_a_count_of_samples_not_the_samples():
    experiments = [experiment("candidate", "w1", timeline_with())]

    view = build_view(run("COMPLETED"), experiments, [], now=NOW, worker_alive=False, research=True)

    assert view["research"]["experiments"][0]["outcome"]["timeline"] == {"samples": 20}


# --- persistence -----------------------------------------------------------------------------------------------------

def test_a_finished_experiment_keeps_its_timeline_and_it_can_be_read_back(repo):
    from app.engine.recommendation_evaluation_validation.validation_loop.grant import GrantedCandidate, new_grant
    grant = new_grant(run_id="run-1", plan_digest_value="p" * 64, target_name="kind-test", cluster_uid="uid", namespace="bookinfo",
                      workers=("w1",), candidates=(GrantedCandidate("c1", "y" * 64),), repeats=1, measure_baseline=False,
                      approved_at=datetime(2026, 10, 24, tzinfo=timezone.utc))
    repo.create_run(grant, mode="QUICK", target_kind="DISPOSABLE", workflow_id="wf", plan=PLAN)
    key = ExperimentKey("candidate", "c1", "w1", 1)
    repo.plan_experiment("run-1", key)

    repo.finish_experiment("run-1", ExperimentOutcome(key=key, status=ExperimentStatus.PASS, timeline=timeline_with()))

    stored = repo.experiment_timeline("run-1", "candidate", "c1", "w1", 1)
    assert stored["evidence"] == "MEASURED" and len(stored["samples"]) == 20
    assert repo.experiment_timeline("run-1", "candidate", "c1", "w9", 1) is None


def test_the_live_timeline_is_written_with_the_current_stage_and_dropped_when_the_experiment_finishes(repo):
    from app.engine.recommendation_evaluation_validation.validation_loop.grant import GrantedCandidate, new_grant
    grant = new_grant(run_id="run-2", plan_digest_value="p" * 64, target_name="kind-test", cluster_uid="uid", namespace="bookinfo",
                      workers=("w1",), candidates=(GrantedCandidate("c1", "y" * 64),), repeats=1, measure_baseline=False,
                      approved_at=datetime(2026, 10, 24, tzinfo=timezone.utc))
    repo.create_run(grant, mode="QUICK", target_kind="DISPOSABLE", workflow_id="wf", plan=PLAN)
    observer = DbObserver(repo, "run-2", clock=lambda: NOW)
    key = ExperimentKey("candidate", "c1", "w1", 1)
    observer.experiment_planned(key)
    observer.stage(key, "INJECT_FAILURE", "stopping w1")

    observer.timeline_update(key, timeline_with())
    live = repo.get_run("run-2")["current"]
    assert live["stage"] == "INJECT_FAILURE" and live["timeline"]["samples"]

    observer.stage(key, "RECOVER_WORKER", "starting w1")      # a later stage of the same failure keeps the timeline
    assert repo.get_run("run-2")["current"]["timeline"]["samples"]

    observer.experiment_finished(ExperimentOutcome(key=key, status=ExperimentStatus.PASS))
    observer.stage(None, "RESTORE_DEPLOYMENT", "c1")
    assert "timeline" not in repo.get_run("run-2")["current"]
