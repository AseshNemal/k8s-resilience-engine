"""Runs are stored, the kill budget is durable, the loop runs in the background, and a cut-off run is recovered, never resumed."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.validation import TestTarget, ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation_loop.models import (
    CANDIDATE,
    ExperimentKey,
    RunStatus,
)
from app.engine.recommendation_evaluation_validation.validation_loop.repository import LoopRepository
from app.engine.recommendation_evaluation_validation.validation_loop.worker import LoopWorker
from tests.member4_validation_loop.support import WORKERS, calls, setup

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'loop.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")
    engine = get_engine(url)
    yield LoopRepository(lambda: Session(engine), clock=lambda: NOW)


def planned(repo, **options):
    """A loop wired to the database budget, with its run row created the way the approval route would."""
    loop, cluster, _, armed, candidate = setup(budget=repo, **options)
    grant = loop.authority.grant
    repo.create_run(grant, mode=loop.plan.mode.value, target_kind="DISPOSABLE", workflow_id=loop.workflow.workflow_id,
                    plan=loop.plan.to_dict())
    return loop, cluster, grant


class FakeRecoverer:
    def __init__(self, *, restore_ok=True, revert_ok=True):
        self.restore_ok, self.revert_ok, self.calls = restore_ok, revert_ok, []

    def restore_workers(self):
        self.calls.append("restore_workers")
        return [{"container": "w1", "restored": self.restore_ok, "error": None if self.restore_ok else "docker start failed"}]

    def revert_workloads(self, restore_points):
        self.calls.append(("revert_workloads", restore_points))
        return {"reverted": self.revert_ok, "error": None if self.revert_ok else "patch rejected"}


# --- schema -------------------------------------------------------------------------------------------------------

def test_the_migration_creates_both_tables(repo):
    names = set(inspect(repo._factory().get_bind()).get_table_names())

    assert {"validation_loop_runs", "validation_loop_experiments"} <= names


# --- the durable kill budget ---------------------------------------------------------------------------------------

def test_the_database_budget_refuses_past_the_maximum_and_survives_a_new_repository_object(repo):
    _, _, grant = planned(repo)

    assert repo.reserve(grant.run_id, 1, 3) and repo.reserve(grant.run_id, 2, 3)
    assert repo.reserve(grant.run_id, 1, 3) is False and repo.used(grant.run_id) == 3
    assert LoopRepository(repo._factory).used(grant.run_id) == 3          # a restart does not reset it


def test_a_budget_reservation_for_an_unknown_run_or_a_nonpositive_count_is_refused(repo):
    assert repo.reserve("no-such-run", 1, 3) is False and repo.reserve("no-such-run", 0, 3) is False


def test_the_authority_charges_the_database_before_it_mints(repo):
    loop, _, grant = planned(repo)

    loop.authority.authorize_failure(TestTarget(grant.target_name, "test"), "w1", baseline=False)

    assert repo.used(grant.run_id) == 1


# --- storing a run -------------------------------------------------------------------------------------------------

def test_a_run_is_stored_with_its_grant_and_plan_and_no_person(repo):
    _, _, grant = planned(repo)

    run = repo.get_run(grant.run_id)

    assert run["status"] == "PLANNED" and run["approval"]["planDigest"] == grant.plan_digest and run["plan"]["mode"] == "QUICK"
    assert "confirmedBy" not in run["approval"] and run["killsUsed"] == 0


def test_a_planned_experiment_is_a_row_and_a_skipped_one_stays_a_row_with_its_reason(repo):
    _, _, grant = planned(repo)
    key = ExperimentKey(CANDIDATE, "c1", "w1", 1)

    repo.plan_experiment(grant.run_id, key)
    repo.plan_experiment(grant.run_id, key)       # planning twice does not duplicate
    assert [e["status"] for e in repo.experiments(grant.run_id)] == ["PENDING"]

    assert repo.close_open_experiments(grant.run_id, "run aborted") == 1
    [row] = repo.experiments(grant.run_id)
    assert row["status"] == "SKIPPED" and row["outcome"]["reasons"] == ["run aborted"]


def test_an_experiment_that_was_running_when_the_run_was_cut_off_becomes_inconclusive_not_skipped(repo):
    _, _, grant = planned(repo)
    key = ExperimentKey(CANDIDATE, "c1", "w1", 1)
    repo.plan_experiment(grant.run_id, key)
    repo.start_experiment(grant.run_id, key)

    repo.close_open_experiments(grant.run_id, "interrupted")

    assert repo.experiments(grant.run_id)[0]["status"] == "INCONCLUSIVE"


# --- running in the background --------------------------------------------------------------------------------------

def run_in_background(repo, worker=None, **options):
    loop, cluster, grant = planned(repo, **options)
    worker = worker or LoopWorker(repo)
    worker.start(grant.run_id, lambda observer, abort: _rewire(loop, observer, abort), FakeRecoverer())
    worker.join(10)
    return worker, loop, cluster, grant


def _rewire(loop, observer, abort):
    loop._observer, loop._abort = observer, abort
    return loop


def test_a_background_run_persists_progress_results_and_a_final_status(repo):
    worker, _, cluster, grant = run_in_background(repo)

    run = repo.get_run(grant.run_id)
    experiments = repo.experiments(grant.run_id)
    assert run["status"] == "COMPLETED" and run["result"]["winner"] is not None and run["killsUsed"] == 3
    assert [e["status"] for e in experiments] == ["PASS", "PASS", "PASS"] and [e["worker"] for e in experiments] == WORKERS
    assert run["current"] is None and run["finishedAt"] is not None
    assert {"RUN_STARTED"} <= {e["event"] for e in run["events"]}
    assert worker.is_working() is False and calls(cluster, "fail:") == ["fail:w1", "fail:w2", "fail:w3"]


def test_the_whole_state_is_readable_after_the_worker_and_the_process_are_gone(repo):
    _, _, _, grant = run_in_background(repo)

    reopened = LoopRepository(repo._factory)

    assert reopened.get_run(grant.run_id)["status"] == "COMPLETED" and len(reopened.experiments(grant.run_id)) == 3
    assert len(reopened.observed_durations()) == 3 or reopened.observed_durations() == []      # only finished verdicts count


def test_a_second_run_is_refused_while_one_is_running_or_needs_a_person(repo):
    _, _, grant = planned(repo)
    repo.set_status(grant.run_id, RunStatus.NEEDS_MANUAL_RESTORE)
    other_loop, *_ = setup()
    worker = LoopWorker(repo)

    with pytest.raises(ValidationRefusedError) as refused:
        worker.start("another", lambda observer, abort: other_loop)
    assert refused.value.code == "RUN_BLOCKED"


def test_abort_is_read_from_the_database_by_the_running_loop(repo):
    loop, cluster, grant = planned(repo)
    repo.set_status(grant.run_id, RunStatus.RUNNING)
    assert repo.request_abort(grant.run_id) is True

    loop._abort = lambda: repo.abort_requested(grant.run_id)
    result = loop.run()

    assert result.status is RunStatus.INTERRUPTED and calls(cluster, "fail:") == []
    assert repo.get_run(grant.run_id)["status"] == "ABORTING"


def test_abort_of_a_run_that_is_not_running_is_a_no_op(repo):
    _, _, grant = planned(repo)

    assert repo.request_abort(grant.run_id) is False


def test_an_unexpected_crash_in_the_loop_closes_open_rows_and_recovers_instead_of_leaving_the_run_live(repo):
    loop, cluster, grant = planned(repo, cluster_options={})
    recoverer = FakeRecoverer()
    worker = LoopWorker(repo)

    def exploding(observer, abort):
        raise RuntimeError("database went away")

    worker.start(grant.run_id, exploding, recoverer)
    worker.join(10)

    run = repo.get_run(grant.run_id)
    assert run["status"] == "INTERRUPTED" and run["recovery"]["complete"] is True
    assert recoverer.calls[0] == "restore_workers" and any("RUN_CRASHED" == e["event"] for e in run["events"])


# --- recovery is not resumption --------------------------------------------------------------------------------------

def live_run_left_behind(repo):
    """What a backend that died mid-run leaves: a RUNNING run, an experiment mid-failure, and stored undo information."""
    _, _, grant = planned(repo)
    key = ExperimentKey(CANDIDATE, "c1", "w1", 1)
    repo.set_status(grant.run_id, RunStatus.RUNNING)
    repo.plan_experiment(grant.run_id, key)
    repo.plan_experiment(grant.run_id, ExperimentKey(CANDIDATE, "c1", "w2", 1))
    repo.start_experiment(grant.run_id, key)
    repo.save_restore_points(grant.run_id, {"reviews": {"replicas": 1}})
    assert repo.reserve(grant.run_id, 1, 3)
    return grant


def test_a_restarted_backend_recovers_a_live_run_by_restoring_and_reverting_only(repo):
    grant = live_run_left_behind(repo)
    recoverer = FakeRecoverer()

    recovered = LoopWorker(repo).reconcile(recoverer)

    run = repo.get_run(grant.run_id)
    assert recovered == [grant.run_id] and run["status"] == "INTERRUPTED"
    assert recoverer.calls == ["restore_workers", ("revert_workloads", {"reviews": {"replicas": 1}})]
    assert run["recovery"]["complete"] is True and run["recovery"]["nodesStopped"] == 0
    assert run["restorePoints"] is None
    assert [e["status"] for e in repo.experiments(grant.run_id)] == ["INCONCLUSIVE", "SKIPPED"]


def test_recovery_never_repeats_a_node_failure_and_the_budget_is_untouched(repo):
    grant = live_run_left_behind(repo)

    LoopWorker(repo).reconcile(FakeRecoverer())

    assert repo.used(grant.run_id) == 1      # the one failure that happened; recovery spent nothing and killed nothing


def test_the_outcome_is_recorded_before_another_run_may_start(repo):
    grant = live_run_left_behind(repo)
    worker = LoopWorker(repo)
    other_loop, *_ = setup()

    with pytest.raises(ValidationRefusedError):
        worker.start("next", lambda observer, abort: other_loop)          # still live: blocked
    worker.reconcile(FakeRecoverer())
    assert repo.blocking_run() is None                                    # recovered and recorded: the way is clear


def test_a_recovery_that_cannot_complete_leaves_the_run_needing_a_person_and_blocks_the_next(repo):
    grant = live_run_left_behind(repo)

    LoopWorker(repo).reconcile(FakeRecoverer(restore_ok=False))

    run = repo.get_run(grant.run_id)
    assert run["status"] == "NEEDS_MANUAL_RESTORE" and run["recovery"]["complete"] is False
    assert run["restorePoints"] == {"reviews": {"replicas": 1}}          # kept so it can be tried again
    assert repo.blocking_run()["runKey"] == grant.run_id


def test_a_failed_revert_also_needs_a_person_and_can_be_retried_to_success(repo):
    grant = live_run_left_behind(repo)
    worker = LoopWorker(repo)
    worker.reconcile(FakeRecoverer(revert_ok=False))
    assert repo.get_run(grant.run_id)["status"] == "NEEDS_MANUAL_RESTORE"

    worker.recover_run(grant.run_id, FakeRecoverer())

    assert repo.get_run(grant.run_id)["status"] == "INTERRUPTED" and repo.blocking_run() is None


def test_reconcile_runs_once_per_process_and_ignores_runs_that_are_not_live(repo):
    _, _, grant = planned(repo)                    # PLANNED, not live
    worker = LoopWorker(repo)
    recoverer = FakeRecoverer()

    assert worker.reconcile(recoverer) == [] and worker.reconcile(recoverer) == [] and recoverer.calls == []
