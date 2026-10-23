"""What the page may say about a run: counts not guesses, nothing claimed restored without evidence, stale and abort made explicit."""

from datetime import datetime, timedelta, timezone

import pytest

from app.engine.recommendation_evaluation_validation.validation_loop.view import build_view

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
PLAN = {"mode": {"mode": "QUICK", "workers": ["w1", "w2"], "repeats": 1, "measureBaseline": False, "preliminary": True},
        "workflowBasis": "FROZEN",
        "candidates": [{"id": "c1", "strategy": "TOPOLOGY_SPREAD", "service": "reviews"}]}


def run(status="RUNNING", **changes):
    base = {"runKey": "r1", "status": status, "targetKind": "DISPOSABLE", "targetName": "kind-test", "clusterUid": "uid", "namespace": "bookinfo",
            "workflowId": "wf", "plan": PLAN, "approval": {"maxFailures": 2}, "killsUsed": 1, "abortRequested": False, "current": None,
            "result": None, "recovery": None, "stopReason": None, "events": [], "startedAt": (NOW - timedelta(seconds=30)).isoformat(),
            "finishedAt": None, "heartbeatAt": (NOW - timedelta(seconds=5)).isoformat()}
    return {**base, **changes}


def experiment(worker, status, **outcome):
    return {"kind": "candidate", "candidateId": "c1", "worker": worker, "repeat": 1, "status": status, "outcome": outcome or None,
            "durationS": 60.0}


def test_progress_is_a_count_of_experiments_and_there_is_no_estimate_without_history():
    view = build_view(run(), [experiment("w1", "PASS"), experiment("w2", "RUNNING")], [], now=NOW, worker_alive=True)

    assert view["metrics"]["progress"] == {"done": 1, "total": 2} and view["metrics"]["finished"] == 1
    assert view["estimate"] is None and "percent" not in str(view).lower()


def test_an_estimate_appears_only_from_experiments_that_ran_and_says_how_many():
    view = build_view(run(), [experiment("w1", "PASS")], [60.0, 120.0], now=NOW, worker_alive=True)

    assert view["estimate"]["basedOnExperiments"] == 2 and view["estimate"]["remainingExperiments"] == 1


def test_the_matrix_has_a_cell_per_candidate_and_worker_with_pending_shown_as_pending():
    view = build_view(run(), [experiment("w1", "PASS")], [], now=NOW, worker_alive=True)

    assert [(c["worker"], c["status"]) for c in view["matrix"]["rows"][0]["cells"]] == [("w1", "PASS"), ("w2", "PENDING")]


@pytest.mark.parametrize("states, expected", [(["PASS"], "PASS"), (["FAIL"], "FAIL"), (["RUNNING"], "RUNNING"),
                                              (["INCONCLUSIVE"], "INCONCLUSIVE"), (["SKIPPED"], "SKIPPED")])
def test_every_state_the_matrix_can_show_comes_from_a_recorded_experiment(states, expected):
    view = build_view(run(), [experiment("w1", states[0]), experiment("w2", "PASS")], [], now=NOW, worker_alive=True)

    assert view["matrix"]["rows"][0]["cells"][0]["status"] == expected


def test_the_current_operation_and_stages_show_only_while_the_run_is_going():
    current = {"stage": "INJECT_FAILURE", "candidateId": "c1", "worker": "w1", "repeat": 1, "detail": "stopping w1"}

    live = build_view(run(current=current), [], [], now=NOW, worker_alive=True)
    done = build_view(run("COMPLETED", current=current), [], [], now=NOW, worker_alive=False)

    assert live["current"]["worker"] == "w1" and [s["id"] for s in live["stages"] if s["active"]] == ["INJECT_FAILURE"]
    assert done["current"] is None and not any(s["active"] for s in done["stages"])
    assert [s["label"] for s in live["stages"]] == ["Prepare", "Apply candidate", "Verify rollout", "Inject failure", "Probe",
                                                    "Recover worker", "Restore deployment"]


def test_while_aborting_the_page_says_so_and_does_not_claim_recovery():
    view = build_view(run("ABORTING", abortRequested=True), [], [], now=NOW, worker_alive=True)

    assert view["statusLabel"] == "Abort requested — recovering worker" and view["abort"]["label"] == view["statusLabel"]
    assert view["safety"]["level"] == "warning" and "verified" in view["safety"]["message"]
    assert view["abort"]["available"] is False


def test_a_run_that_stopped_updating_is_shown_as_stale_not_as_running():
    old = (NOW - timedelta(seconds=600)).isoformat()

    view = build_view(run(heartbeatAt=old), [], [], now=NOW, worker_alive=False)

    assert view["stale"] is True and "may have stopped" in view["statusLabel"]
    assert build_view(run(heartbeatAt=old), [], [], now=NOW, worker_alive=True)["stale"] is False


def test_restoration_is_confirmed_only_by_recorded_evidence():
    ended = run("COMPLETED", result={"winner": "c1", "candidates": [{"candidateId": "c1", "status": "PASSED", "reverted": True}]},
                finishedAt=NOW.isoformat())
    verified = [experiment("w1", "PASS", recovery={"verified": True}), experiment("w2", "PASS", recovery={"verified": True})]
    unverified = [experiment("w1", "PASS", recovery={"verified": False})]

    assert build_view(ended, verified, [], now=NOW)["safety"]["level"] == "ok"
    assert build_view(ended, unverified, [], now=NOW)["safety"]["workersRestored"] == "not-confirmed"
    assert build_view(ended, unverified, [], now=NOW)["safety"]["level"] == "warning"


def test_a_run_that_needs_a_person_is_an_alert_with_the_command_to_run():
    recovery = {"complete": False, "workers": [{"container": "kind-worker", "restored": False}], "workloads": {"reverted": False}}

    safety = build_view(run("NEEDS_MANUAL_RESTORE", recovery=recovery), [], [], now=NOW)["safety"]

    assert safety["level"] == "alert" and "docker start kind-worker" in safety["actions"]


def test_the_winner_improvement_and_model_mismatches_are_in_the_result_for_both_views():
    result = {"winner": "c1", "stopReason": None, "killsUsed": 2, "candidates": [{"candidateId": "c1", "status": "PASSED", "reason": None, "reverted": True}]}
    comparison = {"blastRadius": {"observedBefore": 100.0, "observedAfter": 0.0, "baselineBasis": "DERIVED_FROM_PLACEMENT"}}
    experiments = [experiment("w1", "PASS", comparison=comparison, modelAgrees=True),
                   experiment("w2", "PASS", comparison=comparison, modelAgrees=False, predicted={"workflowSurvives": False},
                              readiness={"holds": True}, probeDisagreesWithReadiness=True)]

    view = build_view(run("COMPLETED", result=result), experiments, [], now=NOW)

    assert view["result"]["winner"] == "c1" and view["result"]["improvement"]["baselineBasis"] == "DERIVED_FROM_PLACEMENT"
    assert "not measured" in view["result"]["improvement"]["basisNote"]
    assert view["result"]["modelMismatches"] == [{"candidateId": "c1", "worker": "w2", "repeat": 1, "predictedSurvives": False,
                                                  "readinessHolds": True}]
    assert view["result"]["probeFindings"] == [{"candidateId": "c1", "worker": "w2", "repeat": 1}]


def test_no_winner_means_no_improvement_is_claimed():
    result = {"winner": None, "candidates": [{"candidateId": "c1", "status": "INCONCLUSIVE", "reason": "x", "reverted": True}]}

    assert build_view(run("COMPLETED", result=result), [experiment("w1", "INCONCLUSIVE")], [], now=NOW)["result"]["improvement"] is None


def test_the_research_view_adds_the_raw_records_and_the_simple_view_does_not():
    assert "research" not in build_view(run(), [], [], now=NOW)
    assert {"experiments", "events", "approval", "plan", "recovery"} <= set(build_view(run(), [], [], now=NOW, research=True)["research"])


def test_another_run_may_start_only_after_a_finished_run_whose_recovery_is_complete():
    assert build_view(run("COMPLETED"), [], [], now=NOW)["canStartAnother"] is True
    assert build_view(run("RUNNING"), [], [], now=NOW)["canStartAnother"] is False
    assert build_view(run("INTERRUPTED", recovery={"complete": False}), [], [], now=NOW)["canStartAnother"] is False
