"""The readable report: built only from the stored run, escapes everything, says what was measured and what was predicted."""

from app.engine.recommendation_evaluation_validation.validation_loop.report import build_report

HOSTILE = "<script>alert(1)</script>"


def timeline():
    samples = [{"t": float(t), "ready": {"web": 0 if 4 <= t < 8 else 1, "db": 3}, "nodes": {"w1": not (5 <= t < 9)},
                "req": {"ok": not (5 <= t < 7), "ms": 40, "status": 200, "why": None}, "cpu": {"w2": 120.0, "w1": None}, "mem": {"w2": 700.0}} for t in range(12)]
    return {"intervalS": 1.0, "baseline": {"web": 1, "db": 3}, "samples": samples, "reads": {},
            "marks": [{"t": 4.0, "kind": "stop"}, {"t": 8.0, "kind": "restore"}, {"t": 9.0, "kind": "node-ready"}],
            "events": [{"t": 5, "reason": "NodeNotReady", "object": "Node/w1", "message": HOSTILE}], "logs": []}


def run(**changes):
    base = {"runKey": "loop-abc", "status": "COMPLETED", "targetKind": "DOCKER_DESKTOP", "targetName": "docker-desktop-research", "clusterUid": "uid-123456789abc",
            "namespace": "bookinfo", "startedAt": "2026-11-19T10:00:00+00:00", "killsUsed": 2, "recovery": None, "result": {}, "stopReason": None,
            "approval": {"maxFailures": 2, "planDigest": "d" * 64},
            "plan": {"mode": {"mode": "LAB", "workers": ["w1", "w2"], "holdS": 30.0}, "candidates": [],
                     "lab": {"impact": {"services": [{"service": "web", "ready": 2, "lost": 2, "left": 0, "state": "down"},
                                                     {"service": "db", "ready": 3, "lost": 1, "left": 2, "state": "degraded"}]}}}}
    return {**base, **changes}


def experiment(**outcome):
    return {"kind": "baseline", "candidateId": None, "worker": "w1+w2", "repeat": 1, "status": "PASS", "durationS": 95.0,
            "outcome": {"timeline": timeline(), "recovery": {"verified": True}, "reasons": [],
                        "predicted": {"lab": True, "nodes": ["w1", "w2"], "servicesDown": ["web"], "heldS": 30.0, "holdRequestedS": 30.0}, **outcome}}


def test_the_report_says_what_was_stopped_what_was_predicted_and_what_was_measured():
    html = build_report(run(), [experiment()])

    assert "Failure test report" in html and "Failure lab" in html
    assert "Workers stopped together: w1, w2" in html and "held 30 s" in html
    assert "Services the placement rule said would lose every ready replica" in html and "Services measured with no ready replica" in html
    assert "<svg" in html and "Is each service up?" in html and "Did the application answer?" in html
    assert "was confirmed Ready again" in html and "carries no name" in html
    assert "goes offline" in html and "keeps 2 of 3" in html


def test_everything_from_the_record_is_escaped_and_the_page_runs_no_script():
    html = build_report(run(targetName=HOSTILE), [experiment()])

    assert HOSTILE not in html and "&lt;script&gt;" in html
    assert "<script" not in html


def test_a_run_that_could_not_restore_a_worker_says_so_at_the_top():
    html = build_report(run(status="NEEDS_MANUAL_RESTORE"), [experiment(recovery={"verified": False})])

    assert "Something could not be restored" in html and "docker start" in html and "was <b>not</b> confirmed Ready again" in html


def test_a_run_with_no_finished_experiment_does_not_pretend_to_have_results():
    html = build_report(run(status="INTERRUPTED", stopReason="Blocked before the stop"), [])

    assert "No experiment ran to a result" in html and "Blocked before the stop" in html
