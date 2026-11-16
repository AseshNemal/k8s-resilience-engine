"""Opt-in workload writes on ONE explicitly authorized disposable cluster. Skipped by default."""
import json
import multiprocessing
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tests.live_release_acceptance import harness as h

pytestmark = [pytest.mark.live_cluster, pytest.mark.skipif(
    not os.environ.get("RESILIENCE_RELEASE_APPROVED_UID"), reason="Explicit release acceptance authorization is required")]


def test_real_api_apply_keep_reset_partial_failure_process_restart_and_persistence(monkeypatch):
    bundle = Path(os.environ["RESILIENCE_RELEASE_BUNDLE"]).resolve()
    uid = os.environ["RESILIENCE_RELEASE_APPROVED_UID"]
    h.environment(bundle, uid)
    from alembic import command
    from alembic.config import Config
    from app.engine.recommendation_evaluation_validation.application import ApplicationRefused
    from app.engine.recommendation_evaluation_validation.application.changes import owned
    from app.engine.recommendation_evaluation_validation.application.repository import AppliedChangeRepository
    command.upgrade(Config("alembic.ini"), "head")
    e = h.engine(bundle)
    gw = h.gateway(bundle, uid)
    baseline = {name: owned(d) for name, d in gw.read_deployments(h.NAMESPACE).items()}
    evidence = {"clusterUid": uid, "faultInjected": False, "scenarios": []}
    def record(name):
        evidence["scenarios"].append(name)
        (bundle / "evidence.json").write_text(json.dumps(evidence, indent=2))
    # The actual approval service creates and decides a binding; no synthetic provenance or manufactured approval row.
    with Session(e) as db:
        candidate, operator, evaluate = h.approved(bundle, db)
        change = h.service(db, gw).apply(candidate, operator, evaluate=evaluate)
        h.settled(db, gw, candidate)
        with pytest.raises(ApplicationRefused) as stale:
            h.service(db, gw).reset(candidate, operator, expected_application_id="stale-application")
        assert stale.value.code == "APPLICATION_CHANGED"
        from dataclasses import replace
        before_edit = gw.read_deployments(h.NAMESPACE)["frontend"]
        gw.patch_deployment({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {
            "namespace": h.NAMESPACE, "name": "frontend"}, "spec": {"replicas": before_edit.replicas + 1}},
            expect_resource_version=before_edit.resource_version)
        with pytest.raises(ApplicationRefused) as outside:
            h.service(db, gw).reset(candidate, operator, expected_application_id=change.application_id)
        assert outside.value.code == "EXTERNAL_CHANGE"
        current = gw.read_deployments(h.NAMESPACE)["frontend"]
        gw.restore_deployment(replace(before_edit, resource_version=current.resource_version))
        import time
        for _ in range(120):
            if gw.rollout_complete(h.NAMESPACE, ["frontend"]): break
            time.sleep(1)
        else: pytest.fail("The acceptance outside-edit reversal did not settle")
        record("stale request/outside edit refused; controlled test edit reverted")
        h.service(db, gw).keep(candidate, operator, expected_application_id=change.application_id)
        record("approved/apply/rollout/keep")
    e.dispose()  # reopen storage; old request sessions are not reused
    e = h.engine(bundle)
    with Session(e) as db:
        assert AppliedChangeRepository(db).get(change.application_id).status == "KEPT"
        h.service(db, gw).reset(candidate, operator, expected_application_id=change.application_id)
        h.settled(db, gw, candidate)
        record("persisted keep/reset")
    assert {n: owned(d) for n, d in gw.read_deployments(h.NAMESPACE).items()} == baseline
    # After reset, recompute from real placement; an earlier prediction is not reused as fresh evidence.
    with Session(e) as db:
        candidate, operator, evaluate = h.approved(bundle, db)
        change = h.service(db, gw).apply(candidate, operator, evaluate=evaluate)
        h.settled(db, gw, candidate)
        with pytest.raises(ApplicationRefused):
            h.service(db, h.gateway(bundle, uid, mode="partial")).reset(candidate, operator,
                expected_application_id=change.application_id)
        record("partial reset retained")
    with Session(e) as db:
        h.service(db, gw).reset(candidate, operator, expected_application_id=change.application_id)
        h.settled(db, gw, candidate)
        record("partial reset recovered")
        candidate, operator, evaluate = h.approved(bundle, db)
        change = h.service(db, gw).apply(candidate, operator, evaluate=evaluate)
        h.settled(db, gw, candidate)
    e.dispose()
    process = multiprocessing.get_context("spawn").Process(target=h.crash_reset,
        args=(str(bundle), uid, candidate, operator, change.application_id))
    process.start(); process.join(45)
    if process.is_alive():
        process.kill(); process.join(); pytest.fail("Child backend did not reach the planned crash; preserve evidence")
    assert process.exitcode == 86
    e = h.engine(bundle)
    with Session(e) as db:
        h.service(db, gw).reset(candidate, operator, expected_application_id=change.application_id)
        h.settled(db, gw, candidate)
        record("actual child-process crash/durable reset recovery")
    assert {n: owned(d) for n, d in gw.read_deployments(h.NAMESPACE).items()} == baseline
    record("baseline owned configuration restored")
