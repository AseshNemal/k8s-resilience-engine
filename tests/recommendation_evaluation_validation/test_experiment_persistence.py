"""Validation runs are stored as experiment records in which the prediction and the observation never mix."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.validation import (
    APPLY,
    FAIL_NODE,
    RunState,
    ValidationPlan,
    ValidationRefusedError,
    ValidationRun,
    authorize,
)
from app.engine.recommendation_evaluation_validation.validation.cluster_gateway import REAL_CLUSTER, TEST_DOUBLE
from app.engine.recommendation_evaluation_validation.validation.experiment_record import ExperimentRecorder
from app.engine.recommendation_evaluation_validation.validation.experiment_repository import (
    ExperimentAlreadyFinalizedError,
    ExperimentExistsError,
    ExperimentRepository,
    PredictionAlteredError,
)
from tests.support.member4_k8s_setup import Setup

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def session(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(config, "head")
    with Session(get_engine(url)) as db:
        yield db


def recorded_run(session, setup=None, *, real=False):
    s = setup or Setup()
    if real:
        s.gateway.evidence_class = s.fault.evidence_class = REAL_CLUSTER
    approval, live, _ = s.proposal()
    executor = s.executor()
    plan = ValidationPlan.from_approval(approval, live, s.scenario.workflow, executor.target)
    run = ValidationRun(plan, executor)
    recorder = ExperimentRecorder(ExperimentRepository(session), clock=lambda: NOW)
    experiment_id = recorder.start(plan, approval, executor, run.thresholds)
    result = recorder.execute(run, experiment_id, authorize(plan, APPLY, "dev"), authorize(plan, FAIL_NODE, "dev"))
    return s, plan, approval, experiment_id, ExperimentRepository(session), result


def test_the_migration_creates_the_table_with_a_unique_experiment_id(session):
    inspector = inspect(session.get_bind())

    assert inspector.has_table("validation_runs")
    columns = {c["name"] for c in inspector.get_columns("validation_runs")}
    assert {"experiment_id", "predicted", "observed", "comparison", "prediction_error", "evidence_class",
            "cluster", "before_evidence", "proposal", "failure", "events", "provenance"} <= columns
    assert any(set(u["column_names"]) == {"experiment_id"} for u in inspector.get_unique_constraints("validation_runs")) \
        or any(i["unique"] and i["column_names"] == ["experiment_id"] for i in inspector.get_indexes("validation_runs"))


def test_a_planned_experiment_holds_the_prediction_and_nothing_observed(session):
    s = Setup()
    approval, live, text = s.proposal()
    executor = s.executor()
    plan = ValidationPlan.from_approval(approval, live, s.scenario.workflow, executor.target)

    experiment_id = ExperimentRecorder(ExperimentRepository(session), clock=lambda: NOW).start(
        plan, approval, executor, ValidationRun(plan, executor).thresholds)
    record = ExperimentRepository(session).get(experiment_id)

    assert record.status == "PLANNED" and record.validated is None and record.is_measurement is False
    assert record.observed is None and record.comparison is None and record.prediction_error is None
    assert record.predicted["failedNode"] == "w1" and record.predicted["blastRadiusAfter"] == pytest.approx(0.0)
    assert record.predicted["blastRadiusBefore"] == pytest.approx(100.0) and record.predicted["riskReduction"] == pytest.approx(100.0)
    assert record.predicted["workflowSurvivesAfter"] is True and record.predicted["serviceSurvivalAfter"]["reviews"] is True
    assert record.predicted["placement"]["reviews"] == {"w1": 1, "w2": 1, "w3": 1}
    # the exact YAML and the approval it was approved under
    assert record.proposal["yaml"] == text and record.proposal["approval"]["id"] == approval.id
    assert record.proposal["approval"]["decidedBy"] == "test" and record.proposal["yamlSha256"] == plan.yaml_sha256
    assert record.proposal["fingerprint"] == plan.predicted.fingerprint
    # who, where, what
    assert record.cluster["target"] == "kind-resilience-test" and record.cluster["cluster"]["kubeSystemUid"]
    assert record.workflow["mandatoryServices"] == ["productpage", "details", "reviews"]
    assert record.before_evidence["failedNode"] == "w1" and record.before_evidence["deploymentBefore"]["services"]


def test_a_completed_run_stores_the_observation_apart_from_the_prediction(session):
    s, plan, _, experiment_id, repo, result = recorded_run(session)
    planned_predicted = json.loads(json.dumps(repo.get(experiment_id).predicted))

    record = repo.get(experiment_id)

    assert result.state is RunState.COMPLETED
    assert record.status == "COMPLETED" and record.validated is True and record.is_measurement is True
    assert record.prediction_error == pytest.approx(0.0)
    assert record.predicted == planned_predicted                      # the prediction is what it was before the run
    assert record.observed["afterFailure"]["blastRadius"] == pytest.approx(0.0)
    assert record.observed["afterFailure"]["workflowSurvives"] is True
    assert record.observed["afterFailure"]["serviceSurvival"]["reviews"] is True
    assert record.observed["baseline"]["basis"] == "DERIVED_FROM_PLACEMENT"
    assert record.observed["placement"]["reviews"] == {"w1": 1, "w2": 1, "w3": 1}
    assert set(record.observed["graphs"]) == {"baseline", "placed", "afterFailure"}
    assert not (set(record.observed) & {"predicted", "blastRadiusAfter", "fingerprint"})      # nothing predicted leaks in
    assert record.comparison["blastRadius"]["absoluteError"] == pytest.approx(0.0)
    assert record.failure == {"runEnded": "COMPLETED", "stopReason": None, "needsManualRestore": False,
                              "workloadsReverted": True, "nodeRestored": True}
    assert [e["event"] for e in record.events][:1] == ["PLANNED"] and "WORKLOADS_REVERTED" in [e["event"] for e in record.events]
    assert record.finished_at is not None and record.started_at is not None


def test_a_stored_record_names_what_each_side_is_derived_from(session):
    _, _, _, experiment_id, repo, _ = recorded_run(session)

    provenance = repo.get(experiment_id).provenance

    assert provenance["predictionBasis"].startswith("Component 3 predict_failure_impact")
    assert "readiness" in provenance["observationBasis"] and "not an end-to-end request probe" in provenance["observationBasis"]
    assert provenance["thresholds"]["blast_radius_tolerance"] == pytest.approx(5.0)
    assert provenance["approvalProvenance"]["source"] == "live-discovery"
    assert provenance["planDigest"]


def test_a_run_that_could_not_measure_has_no_verdict_and_no_error_figure(session):
    s = Setup(fault_options=dict(works=False), executor_options=dict(failure_detect_timeout_s=30.0))

    _, _, _, experiment_id, repo, result = recorded_run(session, s)
    record = repo.get(experiment_id)

    assert result.state is RunState.FAILED
    assert record.status == "FAILED" and record.validated is None and record.prediction_error is None
    assert record.is_measurement is False and record.comparison is None
    assert record.failure["stopReason"] and "NotReady" in record.failure["stopReason"]
    assert record.failure["workloadsReverted"] is True
    assert "afterFailure" not in record.observed and "placed" in record.observed["graphs"]      # what was seen is kept


def test_an_unsettled_run_is_inconclusive_with_the_reason_kept(session):
    s = Setup(gateway_options=dict(pending_after_patch=True), executor_options=dict(settle_timeout_s=60.0))

    _, _, _, experiment_id, repo, _ = recorded_run(session, s)
    record = repo.get(experiment_id)

    assert record.status == "INCONCLUSIVE" and record.validated is None
    assert "did not settle" in record.failure["stopReason"]


def test_a_run_that_leaves_the_cluster_dirty_says_so_in_the_record(session):
    s = Setup(fault_options=dict(restores=False), executor_options=dict(restore_timeout_s=30.0))

    _, _, _, experiment_id, repo, _ = recorded_run(session, s)
    record = repo.get(experiment_id)

    assert record.failure["needsManualRestore"] is True and record.failure["nodeRestored"] is False


def test_a_refused_step_is_recorded_as_refused_and_the_error_propagates(session):
    s = Setup(executor_options=dict(writes_enabled=False))
    approval, live, _ = s.proposal()
    executor = s.executor()
    plan = ValidationPlan.from_approval(approval, live, s.scenario.workflow, executor.target)
    run = ValidationRun(plan, executor)
    recorder = ExperimentRecorder(ExperimentRepository(session), clock=lambda: NOW)
    experiment_id = recorder.start(plan, approval, executor, run.thresholds)

    with pytest.raises(ValidationRefusedError):
        recorder.execute(run, experiment_id, authorize(plan, APPLY, "dev"), authorize(plan, FAIL_NODE, "dev"))
    record = ExperimentRepository(session).get(experiment_id)

    assert record.status == "REFUSED" and record.validated is None and record.failure["stopReason"].startswith("WRITES_DISABLED")


# --- integrity of the store -------------------------------------------------------------------------------------------------

def test_a_finished_experiment_cannot_be_finalized_again(session):
    _, _, _, experiment_id, repo, _ = recorded_run(session)
    record = repo.get(experiment_id)

    with pytest.raises(ExperimentAlreadyFinalizedError):
        repo.finalize(record)


def test_the_prediction_cannot_be_changed_when_the_experiment_is_finalized(session):
    s = Setup()
    approval, live, _ = s.proposal()
    executor = s.executor()
    plan = ValidationPlan.from_approval(approval, live, s.scenario.workflow, executor.target)
    repo = ExperimentRepository(session)
    experiment_id = ExperimentRecorder(repo, clock=lambda: NOW).start(plan, approval, executor, ValidationRun(plan, executor).thresholds)
    record = repo.get(experiment_id)
    tampered = record.__class__(**{**record.__dict__, "predicted": {**record.predicted, "blastRadiusAfter": 100.0}})

    with pytest.raises(PredictionAlteredError):
        repo.finalize(tampered)
    assert repo.get(experiment_id).predicted["blastRadiusAfter"] == pytest.approx(0.0)


def test_an_experiment_id_cannot_be_reused(session):
    s = Setup()
    approval, live, _ = s.proposal()
    executor = s.executor()
    plan = ValidationPlan.from_approval(approval, live, s.scenario.workflow, executor.target)
    repo = ExperimentRepository(session)
    recorder = ExperimentRecorder(repo, clock=lambda: NOW)
    experiment_id = recorder.start(plan, approval, executor, ValidationRun(plan, executor).thresholds)

    with pytest.raises(ExperimentExistsError):
        repo.create(repo.get(experiment_id))


# --- what may be shown as experimental evidence --------------------------------------------------------------------------------

def test_a_run_on_test_doubles_is_stored_but_never_listed_as_a_measurement(session):
    _, _, _, experiment_id, repo, _ = recorded_run(session)

    assert repo.get(experiment_id).evidence_class == TEST_DOUBLE
    assert repo.list_measurements() == []
    assert [r.experiment_id for r in repo.list()] == [experiment_id]


def test_only_a_completed_run_on_a_real_cluster_is_listed_as_a_measurement(session):
    _, _, _, real_id, repo, _ = recorded_run(session, real=True)
    failed = Setup(fault_options=dict(works=False), executor_options=dict(failure_detect_timeout_s=30.0))
    failed.gateway.evidence_class = failed.fault.evidence_class = REAL_CLUSTER
    recorded_run(session, failed)

    measurements = repo.list_measurements()

    assert [r.experiment_id for r in measurements] == [real_id]
    assert measurements[0].evidence_class == REAL_CLUSTER and len(repo.list()) == 2


def test_the_stored_json_round_trips_exactly(session):
    _, _, _, experiment_id, repo, _ = recorded_run(session)
    record = repo.get(experiment_id)

    session.expire_all()

    assert repo.get(experiment_id).predicted == record.predicted and repo.get(experiment_id).observed == record.observed
