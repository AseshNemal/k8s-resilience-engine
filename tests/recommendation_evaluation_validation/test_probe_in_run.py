"""The workflow probe inside a validation run: sampled while the cluster is waited on, and recorded beside the prediction."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.validation import (
    APPLY,
    FAIL_BASELINE,
    FAIL_NODE,
    RunState,
    ValidationPlan,
    ValidationRun,
    authorize,
)
from app.engine.recommendation_evaluation_validation.validation.experiment_record import ExperimentRecorder
from app.engine.recommendation_evaluation_validation.validation.experiment_repository import ExperimentRepository
from tests.support.member4_k8s_setup import Setup

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def session(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'probe.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(config, "head")
    with Session(get_engine(url)) as db:
        yield db


def run_recorded(session, s, *, baseline=False, **probe_options):
    approval, live, _ = s.proposal()
    executor = s.executor(probe_factory=s.probe_factory(**probe_options), failure_window_s=20.0)
    plan = ValidationPlan.from_approval(approval, live, s.scenario.workflow, executor.target)
    run = ValidationRun(plan, executor)
    repo = ExperimentRepository(session)
    recorder = ExperimentRecorder(repo, clock=lambda: NOW)
    experiment_id = recorder.start(plan, approval, executor, run.thresholds)
    result = recorder.execute(run, experiment_id, authorize(plan, APPLY, "dev"), authorize(plan, FAIL_NODE, "dev"),
                              authorize(plan, FAIL_BASELINE, "dev") if baseline else None)
    return executor, result, repo.get(experiment_id)


def test_the_probe_marks_each_step_of_the_failure_in_order_and_samples_the_failure_window(session):
    s = Setup(gateway_options=dict(detection_polls=2, recovery_polls=2))      # the control plane is slow to notice, the node slow to return

    executor, result, _ = run_recorded(session, s)
    report = executor.probe_reports()["afterChange"]

    assert result.state is RunState.COMPLETED
    assert [m["name"] for m in report["marks"]] == ["stop-issued", "pods-unready", "restore-issued", "node-ready"]
    windows = report["windows"]
    assert windows["before"]["samples"] == 5 and windows["failure"]["samples"] >= 3
    assert windows["detection"]["samples"] >= 1 and windows["recovery"]["samples"] >= 1 and windows["after"]["samples"] == 3
    assert report["workflowSurvives"] is True and windows["failure"]["successRatio"] == pytest.approx(1.0)


def test_without_a_probe_the_run_is_unchanged_and_the_record_has_no_probe_section(session):
    s = Setup()
    approval, live, _ = s.proposal()
    executor = s.executor()
    plan = ValidationPlan.from_approval(approval, live, s.scenario.workflow, executor.target)
    run = ValidationRun(plan, executor)
    recorder = ExperimentRecorder(ExperimentRepository(session), clock=lambda: NOW)
    experiment_id = recorder.start(plan, approval, executor, run.thresholds)
    recorder.execute(run, experiment_id, authorize(plan, APPLY, "dev"), authorize(plan, FAIL_NODE, "dev"))

    record = ExperimentRepository(session).get(experiment_id)

    assert executor.probe_reports() == {} and "probes" not in record.observed and "probe" not in record.comparison
    assert record.cluster["probe"] is None


def test_the_probe_is_stored_with_the_observation_never_with_the_prediction(session):
    s = Setup()

    _, _, record = run_recorded(session, s)

    probe = record.observed["probes"]["afterChange"]
    assert probe["sampleCount"] > 8 and probe["workflowSurvives"] is True and probe["spec"]["workflow_id"] == "productpage-view"
    assert "probes" not in record.predicted and "workflowSurvives" not in record.predicted
    assert record.comparison["probe"]["predictedSurvives"] is True and record.comparison["probe"]["probeSurvives"] is True
    assert record.comparison["probe"]["matchesPrediction"] is True and record.comparison["probe"]["agreesWithReadiness"] is True
    assert record.cluster["probe"]["url"] == "http://127.0.0.1:18080/productpage"


def test_a_change_that_did_not_help_is_seen_by_the_probe_as_well_as_by_readiness(session):
    s = Setup(gateway_options=dict(scheduler=None))          # the scheduler leaves the pods where they were

    _, result, record = run_recorded(session, s)

    assert result.validated is False
    assert record.observed["probes"]["afterChange"]["workflowSurvives"] is False
    assert record.comparison["probe"]["matchesPrediction"] is False and record.comparison["probe"]["agreesWithReadiness"] is True


def test_when_readiness_and_a_real_request_disagree_both_are_recorded_and_the_verdict_stays_readiness_based(session):
    s = Setup()

    _, result, record = run_recorded(session, s, broken=("reviews",))      # a ready replica that cannot serve

    assert result.validated is True                                          # Component 3's readiness-derived comparison
    entry = record.comparison["probe"]
    assert entry["readinessSurvives"] is True and entry["probeSurvives"] is False and entry["agreesWithReadiness"] is False
    assert entry["matchesPrediction"] is False and "not instead of it" in entry["note"]


def test_a_measured_baseline_gives_each_failure_its_own_probe_and_an_observed_improvement(session):
    s = Setup()

    executor, result, record = run_recorded(session, s, baseline=True)
    reports = executor.probe_reports()

    assert set(reports) == {"baselineFailure", "afterChange"}
    assert reports["baselineFailure"]["workflowSurvives"] is False          # concentrated on w1: the failure hurt before the change
    assert reports["afterChange"]["workflowSurvives"] is True
    assert record.observed["baseline"]["basis"] == "MEASURED"
    entry = record.comparison["probe"]
    assert entry["baselineProbeSurvives"] is False and entry["observedImprovement"] is True
    assert [m["name"] for m in reports["baselineFailure"]["marks"]] == ["stop-issued", "pods-unready", "restore-issued", "node-ready"]


def test_a_fetch_that_blows_up_costs_samples_not_the_run(session):
    s = Setup()

    executor, result, record = run_recorded(session, s, raises=True)

    assert result.state is RunState.COMPLETED
    probe = record.observed["probes"]["afterChange"]
    assert probe["windows"]["failure"]["failures"] == probe["windows"]["failure"]["samples"] > 0
    assert probe["workflowSurvives"] is False and probe["windows"]["failure"]["errors"] == {"RuntimeError": probe["windows"]["failure"]["samples"]}


def test_a_run_that_never_got_to_the_failure_has_no_probe_verdict(session):
    s = Setup(gateway_options=dict(pending_after_patch=True))

    _, result, record = run_recorded(session, s, )

    assert result.state is RunState.INCONCLUSIVE and record.validated is None
    assert "probes" not in (record.observed or {}) and record.comparison is None
