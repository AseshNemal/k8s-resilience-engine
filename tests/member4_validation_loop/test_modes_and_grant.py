"""The three validation modes, the one-approval run grant, and the durable kill budget."""

from datetime import datetime, timezone

import pytest

from app.engine.recommendation_evaluation_validation.validation import TestTarget, ValidationRefusedError
from app.engine.recommendation_evaluation_validation.validation.models import APPLY, FAIL_BASELINE, FAIL_NODE, STOP_KIND_WORKER
from app.engine.recommendation_evaluation_validation.validation_loop.grant import (
    GrantAuthority,
    GrantedCandidate,
    InMemoryKillBudget,
    max_failures_for,
    new_grant,
)
from app.engine.recommendation_evaluation_validation.validation_loop.models import Mode, resolve_mode

WORKERS = ["w3", "w1", "w2"]
TARGET = TestTarget("kind-test", "test")


def grant(**changes):
    options = dict(run_id="run-1", plan_digest_value="d" * 64, target_name="kind-test", cluster_uid="uid-1", namespace="bookinfo",
                   workers=("w1", "w2", "w3"), candidates=(GrantedCandidate("c1", "sha-1"),), repeats=1, measure_baseline=False,
                   approved_at=datetime(2026, 10, 24, tzinfo=timezone.utc))
    options.update(changes)
    return new_grant(**options)


# --- modes -------------------------------------------------------------------------------------------------------

def test_smoke_is_one_candidate_one_worker_one_repeat_on_the_riskiest_worker_by_default():
    plan = resolve_mode(Mode.SMOKE, WORKERS, riskiest_worker="w2")

    assert (plan.candidates, plan.workers, plan.repeats, plan.measure_baseline) == (1, ("w2",), 1, False)
    assert plan.planned_failures() == 1 and plan.preliminary is True


def test_smoke_lets_the_developer_pick_the_worker_but_only_an_eligible_one():
    assert resolve_mode(Mode.SMOKE, WORKERS, riskiest_worker="w2", smoke_worker="w3").workers == ("w3",)

    with pytest.raises(ValidationRefusedError) as refused:
        resolve_mode(Mode.SMOKE, WORKERS, smoke_worker="control-plane")
    assert refused.value.code == "WORKER_NOT_ELIGIBLE"


def test_quick_is_the_top_candidate_on_every_eligible_worker_once_and_is_preliminary():
    plan = resolve_mode(Mode.QUICK, WORKERS)

    assert (plan.candidates, plan.workers, plan.repeats) == (1, ("w1", "w2", "w3"), 1)
    assert plan.planned_failures() == 3 and plan.preliminary is True


def test_full_is_two_candidates_two_repeats_with_a_measured_baseline_and_is_not_preliminary():
    plan = resolve_mode(Mode.FULL, WORKERS)

    assert (plan.candidates, plan.repeats, plan.measure_baseline) == (2, 2, True)
    assert plan.planned_failures() == 2 * 3 * 2 + 3 and plan.preliminary is False


def test_full_is_configurable_within_its_caps_and_always_measures_a_baseline():
    plan = resolve_mode(Mode.FULL, WORKERS, candidates=3, repeats=3)
    assert (plan.candidates, plan.repeats) == (3, 3)

    for options in ({"candidates": 4}, {"repeats": 4}, {"candidates": 0}):
        with pytest.raises(ValidationRefusedError) as refused:
            resolve_mode(Mode.FULL, WORKERS, **options)
        assert refused.value.code == "RUN_TOO_LARGE"
    with pytest.raises(ValidationRefusedError) as refused:
        resolve_mode(Mode.FULL, WORKERS, measure_baseline=False)
    assert refused.value.code == "BASELINE_REQUIRED"


def test_smoke_and_quick_may_measure_a_baseline_when_asked_and_ignore_candidate_and_repeat_overrides():
    quick = resolve_mode(Mode.QUICK, WORKERS, measure_baseline=True, candidates=3, repeats=3)

    assert quick.measure_baseline is True and (quick.candidates, quick.repeats) == (1, 1)
    assert quick.planned_failures() == 3 + 3
    assert resolve_mode(Mode.SMOKE, WORKERS, measure_baseline=True).planned_failures() == 2


def test_a_plan_with_no_eligible_worker_is_refused():
    with pytest.raises(ValidationRefusedError) as refused:
        resolve_mode(Mode.QUICK, [])
    assert refused.value.code == "NO_ELIGIBLE_WORKER"


# --- the grant ---------------------------------------------------------------------------------------------------

def test_the_failure_budget_is_exactly_what_the_plan_can_cause():
    assert max_failures_for(3, 2, 2, True) == 15 and max_failures_for(3, 1, 1, False) == 3
    assert grant(repeats=2, measure_baseline=True).max_failures == 1 * 3 * 2 + 3


def test_a_grant_carries_no_person_name_only_an_identifier_of_the_approval():
    g = grant()

    assert g.authorizer == f"run-approval:run-1:{'d' * 12}"
    person_fields = {"name", "user", "username", "by", "confirmedBy", "approvedBy", "decidedBy", "requestedBy"}
    assert person_fields.isdisjoint(g.to_dict())


def test_a_grant_survives_storage_unchanged():
    g = grant(desktop_ack=True, acknowledged_workloads=("other/db",))

    assert type(g).from_dict(g.to_dict()) == g


def test_authorizations_are_minted_only_inside_the_grant_and_bound_to_its_plan_and_target():
    authority = GrantAuthority(grant(), InMemoryKillBudget())

    apply = authority.authorize_apply(TARGET, "c1", "sha-1")
    step, stop = authority.authorize_failure(TARGET, "w2", baseline=False)

    assert (apply.action, step.action, stop.action) == (APPLY, FAIL_NODE, STOP_KIND_WORKER)
    assert {apply.plan_digest, step.plan_digest, stop.plan_digest} == {"d" * 64}
    assert {apply.target, step.target, stop.target} == {"kind-test"}
    assert {apply.confirmed_by, step.confirmed_by, stop.confirmed_by} == {grant().authorizer}


@pytest.mark.parametrize("candidate, sha", [("other", "sha-1"), ("c1", "tampered")])
def test_a_candidate_or_yaml_that_was_not_approved_is_refused(candidate, sha):
    with pytest.raises(ValidationRefusedError) as refused:
        GrantAuthority(grant(), InMemoryKillBudget()).authorize_apply(TARGET, candidate, sha)
    assert refused.value.code == "GRANT_CANDIDATE_NOT_APPROVED"


def test_a_worker_outside_the_grant_a_foreign_target_or_a_production_target_is_refused():
    authority = GrantAuthority(grant(), InMemoryKillBudget())

    with pytest.raises(ValidationRefusedError) as worker:
        authority.authorize_failure(TARGET, "control-plane", baseline=False)
    with pytest.raises(ValidationRefusedError) as foreign:
        authority.authorize_failure(TestTarget("another-cluster", "test"), "w1", baseline=False)
    with pytest.raises(ValidationRefusedError) as production:
        authority.authorize_apply(TestTarget("kind-test", "production"), "c1", "sha-1")

    assert worker.value.code == "GRANT_WORKER_NOT_APPROVED"
    assert foreign.value.code == production.value.code == "GRANT_TARGET_MISMATCH"


def test_a_baseline_failure_needs_the_baseline_to_be_part_of_the_approved_run():
    with pytest.raises(ValidationRefusedError) as refused:
        GrantAuthority(grant(measure_baseline=False), InMemoryKillBudget()).authorize_failure(TARGET, "w1", baseline=True)
    assert refused.value.code == "GRANT_BASELINE_NOT_APPROVED"

    step, _ = GrantAuthority(grant(measure_baseline=True), InMemoryKillBudget()).authorize_failure(TARGET, "w1", baseline=True)
    assert step.action == FAIL_BASELINE


def test_the_kill_budget_is_spent_before_arming_and_an_exhausted_budget_refuses():
    budget = InMemoryKillBudget()
    authority = GrantAuthority(grant(workers=("w1",)), budget)         # 1 candidate x 1 worker x 1 repeat = 1 failure

    authority.authorize_failure(TARGET, "w1", baseline=False)
    with pytest.raises(ValidationRefusedError) as refused:
        authority.authorize_failure(TARGET, "w1", baseline=False)

    assert refused.value.code == "GRANT_KILL_BUDGET_EXHAUSTED" and budget.used("run-1") == 1


def test_a_budget_counted_by_one_authority_is_not_reset_by_a_new_one():
    budget = InMemoryKillBudget()      # stands for the database: it outlives the object that minted
    GrantAuthority(grant(workers=("w1",)), budget).authorize_failure(TARGET, "w1", baseline=False)

    with pytest.raises(ValidationRefusedError) as refused:
        GrantAuthority(grant(workers=("w1",)), budget).authorize_failure(TARGET, "w1", baseline=False)
    assert refused.value.code == "GRANT_KILL_BUDGET_EXHAUSTED"


def test_the_research_cluster_authorization_needs_the_acknowledgement_and_the_exact_designated_cluster():
    acknowledged = GrantAuthority(grant(desktop_ack=True), InMemoryKillBudget())

    authorization = acknowledged.research_authorization("uid-1")
    assert authorization is not None and authorization.cluster_uid == "uid-1" and authorization.confirmed_by.startswith("run-approval:")
    assert acknowledged.research_authorization("some-other-uid") is None
    assert GrantAuthority(grant(desktop_ack=False), InMemoryKillBudget()).research_authorization("uid-1") is None
