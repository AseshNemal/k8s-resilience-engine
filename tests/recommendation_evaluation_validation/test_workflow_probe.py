"""The end-to-end probe: requests are judged by what came back, in windows tied to what was done to the cluster."""

import pytest

from app.engine.recommendation_evaluation_validation.validation.workflow_probe import (
    FetchResult,
    ProbeSpec,
    WorkflowProbe,
)

PAGE_OK = "<html>Book Details ... Book Reviews ...</html>"
NO_REVIEWS = "<html>Sorry, product reviews are currently unavailable for this book.</html>"


def spec(**overrides):
    values = dict(workflow_id="productpage-view", url="http://127.0.0.1:18080/productpage",
                  required_text=("Book Details",), forbidden_text=("currently unavailable",), min_samples=3)
    return ProbeSpec(**{**values, **overrides})


class Script:
    """A fetch whose answers are queued, and a clock that advances one second per request."""

    def __init__(self, *answers):
        self.answers, self.calls, self.now = list(answers), 0, 0.0

    def fetch(self, _spec):
        self.calls += 1
        self.now += 1.0
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def clock(self):
        return self.now


def ok(body=PAGE_OK, status=200, latency=12.0):
    return FetchResult(status=status, body=body, latency_ms=latency, error=None)


def probe(script, **spec_overrides):
    return WorkflowProbe(spec(**spec_overrides), script.fetch, clock=script.clock)


def test_a_request_is_ok_only_when_status_and_content_both_say_so():
    script = Script(ok(), ok(NO_REVIEWS), ok(status=503), ok("<html>nothing useful</html>"),
                    FetchResult(status=None, body="", latency_ms=None, error="ReadTimeout"))
    p = probe(script)

    samples = [p.sample() for _ in range(5)]

    assert [s.ok for s in samples] == [True, False, False, False, False]
    assert "currently unavailable" in samples[1].reason          # a 200 that says a dependency is down is a failure
    assert "503" in samples[2].reason and "Book Details" in samples[3].reason
    assert samples[4].error == "ReadTimeout" and samples[4].status is None


def test_a_fetch_that_raises_is_a_failed_sample_not_a_crash():
    p = probe(Script(RuntimeError("boom")))

    sample = p.sample()

    assert sample.ok is False and sample.error == "RuntimeError"


def test_samples_fall_into_windows_by_the_last_mark_before_them():
    script = Script(ok())
    p = probe(script)
    p.sample(); p.sample()                         # before
    p.mark("stop-issued"); p.sample()              # detection
    p.mark("pods-unready"); p.sample(); p.sample(); p.sample()      # failure
    p.mark("restore-issued"); p.sample()           # recovery
    p.mark("node-ready"); p.sample()               # after

    windows = p.report()["windows"]

    assert {name: w["samples"] for name, w in windows.items()} == {
        "before": 2, "detection": 1, "failure": 3, "recovery": 1, "after": 1}
    assert [m["name"] for m in p.report()["marks"]] == ["stop-issued", "pods-unready", "restore-issued", "node-ready"]


def test_survival_comes_from_the_failure_window_alone_and_is_unknown_without_enough_samples():
    script = Script(ok(), ok(), ok(), ok(NO_REVIEWS))
    p = probe(script)
    p.mark("stop-issued"); p.sample()              # before Kubernetes has reacted: reported, never counted for survival
    p.mark("pods-unready")
    assert p.report()["workflowSurvives"] is None                      # no samples in the failure window yet
    p.sample(); p.sample()
    assert p.report()["workflowSurvives"] is None                      # 2 < min_samples 3
    p.sample()

    report = p.report()

    assert report["windows"]["failure"]["samples"] == 3 and report["workflowSurvives"] is False
    assert report["windows"]["failure"]["failures"] == 1 and report["windows"]["failure"]["firstFailureAtS"] is not None


def test_a_workflow_that_answers_every_request_during_the_failure_survives():
    script = Script(ok())
    p = probe(script)
    p.mark("pods-unready")
    for _ in range(4):
        p.sample()

    report = p.report()

    assert report["workflowSurvives"] is True and report["windows"]["failure"]["successRatio"] == pytest.approx(1.0)


def test_the_required_success_ratio_is_configurable():
    script = Script(ok(), ok(), ok(), ok(NO_REVIEWS))
    p = probe(script, min_success_ratio=0.75)
    p.mark("pods-unready")
    for _ in range(4):
        p.sample()

    assert p.report()["workflowSurvives"] is True


def test_the_report_states_what_it_is_and_what_it_is_not():
    p = probe(Script(ok()))
    p.sample()

    report = p.report()

    assert "End-to-end" in report["basis"] and "independent" in report["basis"].lower()
    assert report["spec"]["url"].startswith("http://127.0.0.1") and report["sampleCount"] == 1
    assert report["samples"][0]["ok"] is True and report["isMeasurement"] is False      # a probe alone is never a result


def test_the_spec_rejects_nonsense():
    for bad in (dict(min_success_ratio=0.0), dict(min_success_ratio=1.5), dict(min_samples=0), dict(timeout_s=0),
                dict(method="DELETE"), dict(url="")):
        with pytest.raises(ValueError):
            spec(**bad)
