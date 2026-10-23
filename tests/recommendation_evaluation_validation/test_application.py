"""Apply and Reset: an approved change reaches the connected cluster, and only that change, and can be put back.

The service runs against Deployments that remember what was written (``tests/support/member4_fake_apply_gateway.py``) and a real
migrated database. Nothing here talks to a cluster.
"""

import copy

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation.application import (
    APPLIED,
    FAILED,
    PRODUCTION,
    RESEARCH,
    RESET,
    RESET_FAILED,
    AppliedChangeRepository,
    ApplicationRefused,
    ApplicationService,
)
from app.engine.recommendation_evaluation_validation.approval import (
    ResearchApprovalService,
    has_research_provenance,
    has_verified_provenance,
)
from app.integrations.kubernetes.validation_gateway import ObservedDeployment
from tests.recommendation_evaluation_validation.test_independent_inputs import (  # noqa: F401  (fixtures and helpers)
    declared,
    no_frozen_workflow,
)
from tests.recommendation_evaluation_validation.test_research_approval import approved, evaluate_declared, world  # noqa: F401
from tests.support.member4_approvals import InMemoryApprovals
from tests.support.member4_fake_apply_gateway import FakeApplyGateway

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'apply.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(config, "head")
    with Session(get_engine(url)) as db:
        yield db


def db_deployment(replicas=3):
    return ObservedDeployment("db", "shop", "10", 4, replicas, {"app": "db"}, {"app": "db"})


def other_deployment():
    return ObservedDeployment("web", "shop", "20", 2, 2, {"app": "web"}, {"app": "web"})


@pytest.fixture
def cluster():
    return FakeApplyGateway([db_deployment(), other_deployment()])


@pytest.fixture
def ready(world, session, cluster):
    """A research approval of the selected candidate, the service that applies it, and the evaluation it was approved against."""
    approvals = InMemoryApprovals()
    approval = approved(ResearchApprovalService(approvals), world)
    service = ApplicationService(approvals, AppliedChangeRepository(session), lambda: cluster, tier=RESEARCH,
                                 trusted=has_research_provenance, require_applicable=False, writes_enabled=True)
    return service, approval, approvals


def evaluate(_approval):
    return evaluate_declared()


def test_apply_changes_only_what_was_approved_and_remembers_what_it_replaced(ready, cluster):
    service, approval, _ = ready

    change = service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    assert change.status == APPLIED and change.deployments == ["db"] and change.namespace == "shop"
    assert cluster.deployments["db"].has_hostname_spread and cluster.deployments["db"].replicas == 3
    assert cluster.deployments["web"] == other_deployment()                           # nothing else was touched
    assert len(cluster.patches) == 1 and set(cluster.patches[0]) == {"apiVersion", "kind", "metadata", "spec"}
    before = change.before[0]
    assert before["name"] == "db" and before["replicas"] == 3 and before["topology_spread_constraints"] == []
    assert change.cluster["clusterUid"] == "uid-docker-desktop" and change.applied_by == "asesh"
    assert [e["event"] for e in change.events] == ["applying", "patched", "applied"]


def test_the_state_being_replaced_is_stored_before_the_first_write(ready, cluster, session):
    service, approval, _ = ready
    seen = {}
    original = cluster.patch_deployment

    def spy(document, *, expect_resource_version):
        stored = AppliedChangeRepository(session).latest_for(approval.recommendation_id)
        seen["status"], seen["before"] = stored.status, stored.before
        original(document, expect_resource_version=expect_resource_version)

    cluster.patch_deployment = spy
    service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    assert seen["status"] == "APPLYING" and seen["before"][0]["name"] == "db"


def test_reset_puts_the_deployment_back_to_what_it_was(ready, cluster):
    service, approval, _ = ready
    service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    change = service.reset(approval.recommendation_id, "asesh")

    assert change.status == RESET and change.reset_by == "asesh" and change.reset_at is not None
    assert not cluster.deployments["db"].has_hostname_spread and cluster.deployments["db"].replicas == 3
    assert cluster.restores == ["db"]
    with pytest.raises(ApplicationRefused) as again:
        service.reset(approval.recommendation_id, "asesh")
    assert again.value.code == "NOTHING_TO_RESET"


def test_after_a_reset_the_change_can_be_applied_again(ready, cluster):
    service, approval, _ = ready
    service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)
    service.reset(approval.recommendation_id, "asesh")

    again = service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    assert again.status == APPLIED and cluster.deployments["db"].has_hostname_spread


def test_only_one_change_is_applied_at_a_time(ready):
    service, approval, _ = ready
    service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    with pytest.raises(ApplicationRefused) as refused:
        service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    assert refused.value.code == "APPLICATION_ACTIVE"


# --- what must be true before anything is written ---------------------------------------------------------------------------------------

def test_an_unapproved_recommendation_is_not_applied(world, session, cluster):
    approvals = InMemoryApprovals()
    from tests.recommendation_evaluation_validation.test_research_approval import requested
    selected, _ = requested(ResearchApprovalService(approvals), world)
    service = ApplicationService(approvals, AppliedChangeRepository(session), lambda: cluster, tier=RESEARCH,
                                 trusted=has_research_provenance, require_applicable=False, writes_enabled=True)

    with pytest.raises(ApplicationRefused) as refused:
        service.apply(selected, "asesh", evaluate=evaluate)

    assert refused.value.code == "NOT_APPROVED" and cluster.patches == []


def test_the_production_door_does_not_apply_a_research_approval(ready, session, cluster):
    _, approval, approvals = ready
    production = ApplicationService(approvals, AppliedChangeRepository(session), lambda: cluster, tier=PRODUCTION,
                                    trusted=has_verified_provenance, writes_enabled=True)

    with pytest.raises(ApplicationRefused) as refused:
        production.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    assert refused.value.code == "PROVENANCE_NOT_ACCEPTED" and cluster.patches == []


def test_a_person_must_be_named(ready, cluster):
    service, approval, _ = ready

    with pytest.raises(ApplicationRefused) as refused:
        service.apply(approval.recommendation_id, " ", evaluate=evaluate)

    assert refused.value.code == "APPLIER_REQUIRED" and refused.value.http_status == 422 and cluster.patches == []


def test_a_proposal_whose_binding_changed_since_approval_is_not_applied(ready, cluster):
    service, approval, approvals = ready
    approval.evaluation_snapshot["approvalCheck"]["proposalFingerprint"] = "0" * 64

    with pytest.raises(ApplicationRefused) as refused:
        service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    assert refused.value.code == "APPROVAL_STALE" and cluster.patches == []


def test_yaml_that_is_not_the_approved_text_is_not_applied(ready, cluster):
    service, approval, _ = ready
    approval.yaml_snapshot = approval.yaml_snapshot.replace("maxSkew: 1", "maxSkew: 5")

    with pytest.raises(ApplicationRefused) as refused:
        service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    assert refused.value.code == "YAML_NOT_CURRENT" and cluster.patches == []


def test_a_cluster_whose_identity_cannot_be_read_is_not_written_to(ready):
    service, approval, _ = ready
    service._gateway_factory = lambda: FakeApplyGateway([db_deployment()], uid=None)

    with pytest.raises(ApplicationRefused) as refused:
        service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    assert refused.value.code == "CLUSTER_IDENTITY_UNKNOWN"


def test_a_deployment_missing_from_the_cluster_is_refused_by_the_proposal_whitelist(ready, session):
    service, approval, _ = ready
    empty = FakeApplyGateway([other_deployment()])
    service._gateway_factory = lambda: empty

    with pytest.raises(ApplicationRefused) as refused:
        service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    assert refused.value.code == "PROPOSAL_UNKNOWN_DEPLOYMENT" and empty.patches == []
    assert AppliedChangeRepository(session).latest_for(approval.recommendation_id) is None     # nothing recorded as applied


# --- when the cluster does not cooperate -----------------------------------------------------------------------------------------------

def test_a_deployment_edited_between_reading_and_patching_is_not_overwritten(ready, cluster, session):
    service, approval, _ = ready
    cluster.moved_since_read = "db"

    with pytest.raises(ApplicationRefused) as refused:
        service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    stored = AppliedChangeRepository(session).latest_for(approval.recommendation_id)
    assert refused.value.code == "DEPLOYMENT_CHANGED" and refused.value.retryable
    assert stored.status == FAILED and cluster.patches == [] and cluster.restores == []    # a 409 changed nothing, so nothing is "restored"


def test_a_patch_that_fails_after_being_applied_is_put_back(ready, cluster, session):
    service, approval, _ = ready
    cluster.fail_patch_after_applying = "db"          # the request reached the API server, the answer never came back

    with pytest.raises(ApplicationRefused) as refused:
        service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    stored = AppliedChangeRepository(session).latest_for(approval.recommendation_id)
    assert refused.value.code == "CLUSTER_CONNECTION_FAILED"
    assert stored.status == FAILED and cluster.restores == ["db"] and not cluster.deployments["db"].has_hostname_spread


def test_a_restore_that_also_fails_leaves_a_reset_that_can_be_tried_again(ready, cluster, session):
    service, approval, _ = ready
    cluster.fail_patch_after_applying = "db"
    cluster.fail_restore = True
    with pytest.raises(ApplicationRefused):
        service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)
    assert AppliedChangeRepository(session).latest_for(approval.recommendation_id).status == RESET_FAILED

    with pytest.raises(ApplicationRefused):
        service.reset(approval.recommendation_id, "asesh")          # still failing: the snapshot is kept
    cluster.fail_restore = False
    change = service.reset(approval.recommendation_id, "asesh")

    assert change.status == RESET and not cluster.deployments["db"].has_hostname_spread


def test_a_change_is_never_reset_onto_a_different_cluster(ready, cluster):
    service, approval, _ = ready
    service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)
    other = FakeApplyGateway([db_deployment()], uid="uid-some-other-cluster")
    service._gateway_factory = lambda: other

    with pytest.raises(ApplicationRefused) as refused:
        service.reset(approval.recommendation_id, "asesh")

    assert refused.value.code == "CLUSTER_CHANGED" and other.restores == []


# --- what the page is told ---------------------------------------------------------------------------------------------------------------

def test_status_says_what_can_be_done_next(ready):
    service, approval, _ = ready
    before = service.status(approval.recommendation_id)
    service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)
    during = service.status(approval.recommendation_id)
    service.reset(approval.recommendation_id, "asesh")
    after = service.status(approval.recommendation_id)

    assert (before["canApply"], before["canReset"], before["application"]) == (True, False, None)
    assert (during["canApply"], during["canReset"], during["rolloutComplete"]) == (False, True, True)
    assert during["application"]["status"] == APPLIED and "reset it first" not in (during["reason"] or "")
    assert (after["canApply"], after["canReset"]) == (True, False) and after["application"]["status"] == RESET


def test_status_of_something_never_approved_offers_nothing(world, session, cluster):
    service = ApplicationService(InMemoryApprovals(), AppliedChangeRepository(session), lambda: cluster, tier=RESEARCH,
                                 trusted=has_research_provenance, require_applicable=False, writes_enabled=True)

    status = service.status("rec-unknown")

    assert status["canApply"] is False and status["canReset"] is False and status["application"] is None


# --- armed by the deployment, not by the caller ----------------------------------------------------------------------------------------------

def test_a_service_that_was_not_armed_writes_nothing_and_says_why(ready, cluster):
    service, approval, approvals = ready
    unarmed = ApplicationService(approvals, service.repository, lambda: cluster, tier=RESEARCH, trusted=has_research_provenance,
                                 require_applicable=False)          # the default: off

    for action in (lambda: unarmed.apply(approval.recommendation_id, "asesh", evaluate=evaluate),
                   lambda: unarmed.reset(approval.recommendation_id, "asesh")):
        with pytest.raises(ApplicationRefused) as refused:
            action()
        assert refused.value.code == "CLUSTER_WRITES_NOT_ENABLED" and refused.value.http_status == 403

    status = unarmed.status(approval.recommendation_id)
    assert status["canApply"] is False and status["writesEnabled"] is False and "RESILIENCE_ALLOW_CLUSTER_WRITES" in status["reason"]
    assert cluster.patches == [] and cluster.restores == []
