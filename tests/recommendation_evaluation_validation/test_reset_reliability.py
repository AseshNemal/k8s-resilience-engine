"""Reset is reliable, Keep is a decision, and the ledger knows what is still on a cluster.

Same doubles as ``test_application.py``: the real service against Deployments that remember what was written, and a real
migrated database. Nothing here talks to a cluster.
"""

from dataclasses import replace

import pytest

from app.engine.recommendation_evaluation_validation.application import APPLIED, KEPT, RESET, ApplicationRefused
from app.engine.recommendation_evaluation_validation.application import changes as change_view
from tests.recommendation_evaluation_validation.test_application import (  # noqa: F401  (fixtures and helpers)
    cluster,
    declared,
    evaluate,
    no_frozen_workflow,
    ready,
    session,
    world,
)

HOSTNAME = "kubernetes.io/hostname"


def applied(ready):
    service, approval, _ = ready
    return service, approval.recommendation_id, service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)


# -- what a change set is recorded, and described in words ------------------------------------------------------------------

def test_apply_records_what_it_set_so_a_later_reset_has_something_to_compare(ready):
    _service, _rid, change = applied(ready)

    assert change.after is not None and change.after[0]["name"] == "db"
    assert change.after[0]["topology_spread_constraints"] and change.before[0]["topology_spread_constraints"] == []
    assert "after-not-recorded" not in [e["event"] for e in change.events]


def test_the_status_says_in_words_what_changed_and_what_did_not(ready):
    service, rid, _change = applied(ready)

    rows = service.status(rid)["changes"]

    assert [row["deployment"] for row in rows] == ["db"]
    fields = {f["field"]: f for f in rows[0]["fields"]}
    assert fields["replicas"]["changed"] is False and fields["replicas"]["fromText"] == "3"
    spread = fields["topology_spread_constraints"]
    assert spread["changed"] is True and spread["fromText"] == "none" and "spread across nodes" in spread["toText"]
    assert fields["pod_anti_affinity"]["changed"] is False
    assert rows[0]["summary"].startswith("db: topology spread none → ")


def test_a_change_whose_read_back_failed_says_unknown_never_a_guess(ready, cluster):
    service, approval, _ = ready
    reads = {"n": 0}
    original = cluster.read_deployments

    def flaky(namespace):
        reads["n"] += 1
        if reads["n"] == 2:        # the read straight after the patches
            from app.integrations.kubernetes.client import ConnectionFailedError
            raise ConnectionFailedError("ReadTimeout")
        return original(namespace)

    cluster.read_deployments = flaky
    change = service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)

    assert change.status == APPLIED and change.after is None
    assert "after-not-recorded" in [e["event"] for e in change.events]
    row = change_view.describe(change.before, change.after)[0]
    assert all(f["changed"] is None and f["toText"] is None for f in row["fields"])
    assert row["summary"] == "db: what was set was not recorded"


# -- external drift --------------------------------------------------------------------------------------------------------

def test_reset_refuses_when_someone_else_changed_a_field_this_change_set(ready, cluster):
    service, rid, _change = applied(ready)
    cluster.deployments["db"] = replace(cluster.deployments["db"], topology_spread_constraints=(
        {"maxSkew": 2, "topologyKey": "topology.kubernetes.io/zone", "whenUnsatisfiable": "ScheduleAnyway"},))

    with pytest.raises(ApplicationRefused) as refused:
        service.reset(rid, "asesh")

    assert refused.value.code == "EXTERNAL_CHANGE"
    external = refused.value.detail()["details"]["external"]
    assert [(e["deployment"], e["field"]) for e in external] == [("db", "topology_spread_constraints")]
    assert cluster.restores == [], "nothing was restored"
    assert service.repository.latest_for(rid).status == APPLIED, "the change is still applied and can be reset later"


def test_a_field_changed_outside_that_this_change_did_not_set_is_also_reported(ready, cluster):
    # The change set the spread only, but it OWNS all three fields of that Deployment while applied: a scale by hand is drift.
    service, rid, _change = applied(ready)
    cluster.deployments["db"] = replace(cluster.deployments["db"], replicas=7)

    with pytest.raises(ApplicationRefused) as refused:
        service.reset(rid, "asesh")

    assert refused.value.code == "EXTERNAL_CHANGE" and "replicas" in refused.value.message
    assert cluster.deployments["db"].replicas == 7


def test_reset_keeping_external_changes_restores_only_what_is_still_this_changes(ready, cluster):
    service, rid, _change = applied(ready)
    cluster.deployments["db"] = replace(cluster.deployments["db"], replicas=7)      # scaled by hand since

    change = service.reset(rid, "asesh", external="keep")

    assert change.status == RESET
    assert cluster.deployments["db"].replicas == 7, "the hand-made scale was left exactly as found"
    assert not cluster.deployments["db"].has_hostname_spread, "the spread this change added was removed"
    assert change.rollout["leftAsFound"][0]["field"] == "replicas"
    restoration = service.status(rid)["rollout"]["restoration"]
    assert restoration["verified"] is True and restoration["scope"] == "configuration"
    assert [(i["deployment"], i["field"]) for i in restoration["leftAsFound"]] == [("db", "replicas")]


def test_reset_without_drift_is_unchanged_and_verifies_configuration_only(ready, cluster):
    service, rid, _change = applied(ready)

    service.reset(rid, "asesh")

    restoration = service.status(rid)["rollout"]["restoration"]
    assert restoration["verified"] is True and restoration["leftAsFound"] == [] and restoration["scope"] == "configuration"


def test_an_unknown_external_choice_is_refused_before_anything_is_read(ready):
    service, rid, _change = applied(ready)

    with pytest.raises(ApplicationRefused) as refused:
        service.reset(rid, "asesh", external="overwrite")

    assert refused.value.code == "EXTERNAL_CHOICE_INVALID"


# -- keep ------------------------------------------------------------------------------------------------------------------

def test_keeping_is_a_named_decision_that_writes_nothing_to_the_cluster(ready, cluster):
    service, rid, _change = applied(ready)
    writes = (len(cluster.patches), len(cluster.restores))

    kept = service.keep(rid, "asesh")

    assert kept.status == KEPT and kept.detail["kept"]["by"] == "asesh"
    assert (len(cluster.patches), len(cluster.restores)) == writes
    assert cluster.deployments["db"].has_hostname_spread
    status = service.status(rid)
    assert status["canKeep"] is False and status["canReset"] is True


def test_a_kept_change_no_longer_blocks_and_can_still_be_reset(ready, cluster):
    service, rid, _change = applied(ready)
    service.keep(rid, "asesh")

    assert service._active_here("uid-docker-desktop") == []
    change = service.reset(rid, "asesh")

    assert change.status == RESET and not cluster.deployments["db"].has_hostname_spread


def test_only_an_applied_change_can_be_kept(ready):
    service, approval, _ = ready
    with pytest.raises(ApplicationRefused) as nothing:
        service.keep(approval.recommendation_id, "asesh")
    assert nothing.value.code == "NOTHING_TO_KEEP"

    service.apply(approval.recommendation_id, "asesh", evaluate=evaluate)
    service.reset(approval.recommendation_id, "asesh")
    with pytest.raises(ApplicationRefused) as after_reset:
        service.keep(approval.recommendation_id, "asesh")
    assert after_reset.value.code == "NOTHING_TO_KEEP"


def test_keeping_needs_a_name(ready):
    service, rid, _change = applied(ready)
    with pytest.raises(ApplicationRefused) as refused:
        service.keep(rid, " ")
    assert refused.value.code == "APPLIER_REQUIRED"


# -- the ledger ------------------------------------------------------------------------------------------------------------

def test_the_ledger_is_empty_when_nothing_was_applied(ready):
    service, _approval, _ = ready
    ledger = service.outstanding()
    assert ledger["changes"] == [] and ledger["blocking"] == [] and ledger["resetInProgress"] is False


def test_an_applied_change_is_outstanding_and_blocks_a_disconnect(ready):
    service, rid, _change = applied(ready)

    ledger = service.outstanding()

    assert ledger["connectedClusterUid"] == "uid-docker-desktop"
    row = ledger["changes"][0]
    assert row["recommendationId"] == rid and row["status"] == APPLIED and row["clusterMatch"] == "same"
    assert row["blocksDisconnect"] is True and row["canReset"] is True and ledger["blocking"] == [row]
    assert row["changes"][0]["summary"].startswith("db: topology spread")


def test_a_kept_change_stays_in_the_ledger_but_does_not_block(ready):
    service, rid, _change = applied(ready)
    service.keep(rid, "asesh")

    ledger = service.outstanding()

    assert [row["status"] for row in ledger["changes"]] == [KEPT]
    assert ledger["blocking"] == [] and ledger["changes"][0]["kept"]["by"] == "asesh"


def test_a_change_on_another_cluster_is_listed_but_blocks_nothing_here(ready, cluster):
    service, _rid, _change = applied(ready)
    cluster.uid = "uid-some-other-cluster"

    ledger = service.outstanding()

    row = ledger["changes"][0]
    assert row["clusterMatch"] == "other" and row["blocksDisconnect"] is False and row["canReset"] is False
    assert ledger["blocking"] == []


def test_reconnecting_to_the_same_cluster_shows_the_change_again(ready, cluster):
    service, rid, _change = applied(ready)
    cluster.uid = "uid-some-other-cluster"
    assert service.outstanding()["blocking"] == []

    cluster.uid = "uid-docker-desktop"       # the original cluster is connected again

    ledger = service.outstanding()
    assert [row["recommendationId"] for row in ledger["blocking"]] == [rid]
    assert service.status(rid)["canReset"] is True


def test_a_cluster_that_cannot_be_identified_counts_as_possibly_this_one(ready, cluster):
    service, _rid, _change = applied(ready)
    cluster.unreachable = True

    ledger = service.outstanding()

    assert ledger["connectedClusterUid"] is None
    assert ledger["changes"][0]["clusterMatch"] == "unknown" and ledger["changes"][0]["blocksDisconnect"] is True


def test_a_reset_still_rolling_out_holds_the_connection_until_it_is_seen_to_finish(ready, cluster):
    service, rid, _change = applied(ready)
    cluster.rollout_lag = 2
    service.reset(rid, "asesh")

    assert service.outstanding()["resetInProgress"] is True

    finished = service.outstanding(advance_resets=True)
    while finished["resetInProgress"]:
        finished = service.outstanding(advance_resets=True)
    assert finished["changes"] == [] and finished["blocking"] == []


def test_a_finished_reset_leaves_the_ledger(ready):
    service, rid, _change = applied(ready)
    service.reset(rid, "asesh")
    service.status(rid)       # the rollout is seen to finish

    assert service.outstanding()["changes"] == []


def test_leaving_a_change_at_disconnect_is_written_on_it(ready):
    service, rid, change = applied(ready)

    service.note_left_at_disconnect([change.application_id])

    assert service.repository.latest_for(rid).events[-1]["event"] == "left-at-disconnect"
