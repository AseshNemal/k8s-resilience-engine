"""Apply, rollout, post-change analysis and Reset as one persisted operation.

What these pin: a patch being accepted is not a rollout, a rollout is not an analysis, and none of them is experimental
validation; the baseline fixed before the change survives everything after it; and each failure (cluster unreachable, workflow
edited, analysis failing) says what it is without hiding the applied change or removing Reset.

The service runs against Deployments that remember what was written (``FakeApplyGateway``), a real migrated database, and the
fake cluster Component 2 reads, so the research analyser's recalculation really goes through Component 2 and Component 3.
"""

import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation import live_pipeline
from app.engine.recommendation_evaluation_validation.application import (
    APPLIED,
    RESET,
    AppliedChangeRepository,
    ApplicationRefused,
    ApplicationService,
    ChangeAnalysisRepository,
)
from app.engine.recommendation_evaluation_validation.application import live_analyser, rollout
from app.engine.recommendation_evaluation_validation.application.analysis import compare
from app.engine.recommendation_evaluation_validation.application.live_analyser import ResearchAnalyser
from app.engine.recommendation_evaluation_validation.approval import ResearchApprovalService, has_research_provenance
from app.shared.evidence_errors import EvidenceError
from tests.recommendation_evaluation_validation.test_application import (  # noqa: F401  (fixtures and helpers)
    cluster,
    db_deployment,
    other_deployment,
    session,
)
from tests.recommendation_evaluation_validation.test_independent_inputs import (  # noqa: F401
    DECLARATION,
    REQUESTS,
    concentrated_db,
    declared,
    no_frozen_workflow,
)
from tests.recommendation_evaluation_validation.test_research_approval import approved, evaluate_declared, world  # noqa: F401
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod
from tests.support.member4_approvals import InMemoryApprovals


def spread_db():
    """What Component 2 reads once the change has rolled out: the db replicas on three nodes."""
    return FakeClusterSource(
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod("db-0", "w1", app="db"), pod("db-1", "w2", app="db"), pod("db-2", "w3", app="db"),
                       pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"shop": [deployment("db", 3), deployment("web", 2)]},
        template_specs={"db": REQUESTS, "web": REQUESTS})


def evaluate(_approval):
    return evaluate_declared()


@pytest.fixture
def flow(world, session, cluster, use_cluster):
    approvals = InMemoryApprovals()
    approval = approved(ResearchApprovalService(approvals), world)

    def make(db=None, analyser=None):
        db = db or session
        return ApplicationService(approvals, AppliedChangeRepository(db), lambda: cluster, tier=RESEARCH_TIER,
                                  trusted=has_research_provenance, require_applicable=False, writes_enabled=True,
                                  analyses=ChangeAnalysisRepository(db), analyser=analyser or ResearchAnalyser())

    return SimpleNamespace(make=make, service=make(), rec=approval.recommendation_id, cluster=cluster,
                           use_cluster=use_cluster, session=session, approvals=approvals)


RESEARCH_TIER = "research"


@pytest.fixture
def member3_calls(monkeypatch):
    """Records every call the analyser makes to Component 3's public evaluator, and passes it through."""
    calls = []
    original = live_analyser.predict_failure_impact

    def spy(workflow, deployment_graph, failed_node):
        calls.append({"workflow": workflow.workflow_id, "node": failed_node,
                      "db_nodes": sorted(p.node for s in deployment_graph.services if s.name == "db" for p in s.pods)})
        return original(workflow, deployment_graph, failed_node)

    monkeypatch.setattr(live_analyser, "predict_failure_impact", spy)
    return calls


# --- patch acceptance is not a rollout ----------------------------------------------------------------------------------------


def test_accepted_patches_are_not_a_completed_rollout_and_old_ready_pods_prove_nothing(flow, member3_calls):
    flow.cluster.rollout_lag = 2          # the controller has not observed the new generation; the OLD pods are all ready

    change = flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    first = flow.service.status(flow.rec)

    assert change.status == APPLIED and change.rollout["state"] == "rolling-out"
    assert first["rollout"]["state"] == "rolling-out" and first["rolloutComplete"] is False
    assert "has not observed the latest change" in first["rollout"]["deployments"][0]["reason"]
    assert first["operation"]["stage"] == "rollout" and first["operation"]["inProgress"] is True
    assert first["analysis"]["apply"]["status"] == "PENDING" and first["analysis"]["apply"]["observed"] is None
    assert member3_calls == []            # nothing was recalculated from a topology that has not finished changing
    assert first["states"]["patchesAccepted"] is True and first["states"]["rolloutCompleted"] is False
    assert first["states"]["analysisCompleted"] is False and first["states"]["experimentallyValidated"] is False


def test_replicas_of_the_old_generation_still_present_keep_the_rollout_open(flow):
    flow.cluster.stale_old_pods = 1
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)

    status = flow.service.status(flow.rec)

    assert status["rollout"]["state"] == "rolling-out"
    assert "total 4 of 3" in status["rollout"]["deployments"][0]["reason"]


def test_the_rollout_completes_when_the_deployments_say_so_and_is_analysed_once(flow, member3_calls):
    flow.cluster.rollout_lag = 1
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.service.status(flow.rec)
    flow.use_cluster(spread_db())

    done = flow.service.status(flow.rec)
    again = flow.service.status(flow.rec)

    assert done["rollout"]["state"] == "completed" and done["rolloutComplete"] is True
    assert done["analysis"]["apply"]["status"] == "COMPLETED" and done["operation"]["stage"] == "results"
    assert len(member3_calls) == 1 and member3_calls[0] == {"workflow": "checkout", "node": "w1", "db_nodes": ["w1", "w2", "w3"]}
    assert again["analysis"]["apply"]["analysisId"] == done["analysis"]["apply"]["analysisId"]
    events = [e["event"] for e in again["application"]["events"]]
    assert events.count("rollout-completed") == 1 and events.count("analysis-completed") == 1     # not one per poll


# --- the three-way comparison ---------------------------------------------------------------------------------------------------


def test_before_predicted_and_after_are_kept_apart_and_compared_in_percentage_points(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.use_cluster(spread_db())

    analysis = flow.service.status(flow.rec)["analysis"]["apply"]

    assert analysis["baseline"]["blastRadius"] == 100.0 and analysis["baseline"]["kind"] == "prediction-from-live-topology"
    assert analysis["predicted"]["blastRadius"] == 0.0 and analysis["predicted"]["kind"] == "virtual-simulation"
    assert analysis["observed"]["blastRadius"] == 0.0 and analysis["observed"]["kind"] == "recalculated-from-live-topology"
    assert analysis["comparison"] == {
        "beforeBlastRadius": 100.0, "afterBlastRadius": 0.0, "reductionPoints": 100.0, "direction": "improved",
        "relativeReductionPercent": 100.0, "predictedBlastRadius": 0.0, "predictionDeltaPoints": 0.0}
    assert analysis["baseline"]["placement"]["db"]["pods"] and {p["node"] for p in analysis["baseline"]["placement"]["db"]["pods"]} == {"w1"}
    assert {p["node"] for p in analysis["predicted"]["placement"]["db"]["pods"]} == {"w1", "w2", "w3"}
    assert {p["node"] for p in analysis["observed"]["placement"]["db"]["pods"]} == {"w1", "w2", "w3"}
    assert analysis["baseline"]["failedServices"] and analysis["observed"]["failedServices"] == []


def test_the_result_is_labelled_a_recalculation_never_an_experiment(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.use_cluster(spread_db())

    status = flow.service.status(flow.rec)
    analysis = status["analysis"]["apply"]

    assert analysis["label"].startswith("Post-change risk recalculated from live topology")
    assert "not an experimentally observed workflow failure rate" in analysis["label"]
    text = json.dumps(status).lower()
    for claim in ("experimentally validated", "measured resilience improvement", "validated improvement"):
        assert claim not in text.replace("not an experimentally validated", "")
    assert status["states"]["experimentallyValidated"] is False


def test_the_topology_is_fresh_evidence_captured_after_the_rollout_completed(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.use_cluster(spread_db())

    status = flow.service.status(flow.rec)
    observed = status["analysis"]["apply"]["observed"]

    assert observed["capturedAfterRollout"] is True
    assert datetime.fromisoformat(observed["topologyCapturedAt"]) >= datetime.fromisoformat(status["rollout"]["completedAt"])
    assert observed["topologySnapshotId"] and observed["recalculatedAt"]


def test_the_baseline_is_immutable_and_the_operation_outlives_the_recommendation(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    stored = ChangeAnalysisRepository(flow.session).latest_for(flow.service.repository.latest().application_id, "apply")
    baseline = json.loads(json.dumps(stored.baseline))

    flow.use_cluster(spread_db())
    now = live_pipeline.build_declared_recommendation("checkout", None, "shop")
    assert now.report.selected_recommendation_id is None            # the engine generates nothing for the spread topology now
    status = flow.service.status(flow.rec)

    assert status["application"]["status"] == "APPLIED" and status["canReset"] is True
    assert status["analysis"]["apply"]["baseline"] == baseline and status["analysis"]["apply"]["baseline"]["blastRadius"] == 100.0
    assert status["analysis"]["apply"]["comparison"]["reductionPoints"] == 100.0


def test_a_worse_result_is_reported_as_worse_not_improved(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.use_cluster(concentrated_db())                  # the pods are still all on one node after the rollout

    status = flow.service.status(flow.rec)

    assert status["analysis"]["apply"]["comparison"]["direction"] == "unchanged"
    assert status["analysis"]["apply"]["comparison"]["reductionPoints"] == 0.0
    assert status["operation"]["tone"] == "neutral" and "Risk unchanged" in status["operation"]["headline"]
    assert status["analysis"]["apply"]["comparison"]["predictionDeltaPoints"] == 100.0       # the prediction missed by 100 points


def test_compare_keeps_unknown_unknown_and_handles_a_zero_baseline():
    assert compare({"blastRadius": None}, None, {"blastRadius": 0.0})["reductionPoints"] is None
    assert compare({"blastRadius": 100.0}, None, {"blastRadius": None})["direction"] is None
    zero = compare({"blastRadius": 0.0}, {"blastRadius": 0.0}, {"blastRadius": 0.0})
    assert zero["reductionPoints"] == 0.0 and zero["direction"] == "unchanged" and zero["relativeReductionPercent"] is None
    worse = compare({"blastRadius": 20.0}, {"blastRadius": 0.0}, {"blastRadius": 60.0})
    assert worse["direction"] == "worsened" and worse["reductionPoints"] == -40.0 and worse["relativeReductionPercent"] == -200.0
    assert worse["predictionDeltaPoints"] == 60.0
    assert compare({"blastRadius": 100.0}, None, {"blastRadius": 20.0})["reductionPoints"] == 80.0
    assert compare({"blastRadius": 100.0}, None, {"blastRadius": 20.0})["predictedBlastRadius"] is None


# --- analysis that cannot or did not run ------------------------------------------------------------------------------------------


def test_an_edited_declaration_makes_the_comparison_unavailable_not_misleading(flow, declared):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    declared.write_text(json.dumps({**DECLARATION, "note": "edited after the change"}))
    flow.use_cluster(spread_db())

    status = flow.service.status(flow.rec)
    analysis = status["analysis"]["apply"]

    assert analysis["status"] == "UNAVAILABLE" and analysis["error"]["code"] == "WORKFLOW_CHANGED"
    assert analysis["comparison"] is None and analysis["observed"] is None
    assert "No comparison is made" in status["operation"]["headline"]
    assert status["canReset"] is True and status["application"]["status"] == "APPLIED"


def test_a_different_cluster_makes_the_comparison_unavailable(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.cluster.uid = "uid-another-cluster"

    analysis = flow.service.status(flow.rec)["analysis"]["apply"]

    assert analysis["status"] == "UNAVAILABLE" and analysis["error"]["code"] == "CLUSTER_CHANGED"


def test_the_assessed_node_vanishing_makes_the_comparison_unavailable(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.use_cluster(FakeClusterSource(
        nodes=[node("w2"), node("w3")],
        pods={"shop": [pod("db-0", "w2", app="db"), pod("db-1", "w3", app="db"), pod("db-2", "w3", app="db"),
                       pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"shop": [deployment("db", 3), deployment("web", 2)]}, template_specs={"db": REQUESTS, "web": REQUESTS}))

    analysis = flow.service.status(flow.rec)["analysis"]["apply"]

    assert analysis["status"] == "UNAVAILABLE" and analysis["error"]["code"] == "ASSESSED_NODE_UNAVAILABLE"


class Flaky:
    """Component 2 or 3 failing now: the analyser raises a coded, retryable error until it is told to work."""

    def __init__(self, working):
        self.working, self.failing = working, True

    def plan(self, change, live):
        return self.working.plan(change, live)

    def recalculate(self, basis):
        if self.failing:
            raise EvidenceError("TOPOLOGY_UNAVAILABLE", "Component 2 deployment topology is unavailable", http_status=503,
                                component="failure_impact", retryable=True)
        return self.working.recalculate(basis)


def test_a_failed_analysis_never_hides_the_applied_change_or_removes_reset_and_retry_applies_nothing(flow):
    flaky = Flaky(ResearchAnalyser())
    service = flow.make(analyser=flaky)
    service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.use_cluster(spread_db())
    patches = len(flow.cluster.patches)

    failed = service.status(flow.rec)

    assert failed["analysis"]["apply"]["status"] == "FAILED" and failed["analysis"]["apply"]["error"]["code"] == "TOPOLOGY_UNAVAILABLE"
    assert failed["application"]["status"] == "APPLIED" and failed["canReset"] is True and failed["canRetryAnalysis"] is True
    assert failed["operation"]["nextAction"] == "retry-analysis" and failed["operation"]["stage"] == "analysis"
    assert service.status(flow.rec)["analysis"]["apply"]["status"] == "FAILED"          # a poll does not retry by itself

    flaky.failing = False
    service.retry_analysis(flow.rec)
    retried = service.status(flow.rec)

    assert retried["analysis"]["apply"]["status"] == "COMPLETED" and retried["operation"]["stage"] == "results"
    assert len(flow.cluster.patches) == patches and flow.cluster.restores == []        # nothing was applied or reset by the retry
    assert "analysis-retried" in [e["event"] for e in retried["application"]["events"]]


def test_only_a_failed_analysis_can_be_retried(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.use_cluster(spread_db())
    flow.service.status(flow.rec)

    with pytest.raises(ApplicationRefused) as refused:
        flow.service.retry_analysis(flow.rec)

    assert refused.value.code == "ANALYSIS_NOT_RETRYABLE"


def test_a_retry_before_the_rollout_completes_is_refused(flow):
    flow.cluster.rollout_lag = 5
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)

    with pytest.raises(ApplicationRefused) as refused:
        flow.service.retry_analysis(flow.rec)

    assert refused.value.code in ("ROLLOUT_NOT_COMPLETE", "ANALYSIS_NOT_RETRYABLE")


# --- rollout states -----------------------------------------------------------------------------------------------------------


def test_pods_the_scheduler_cannot_place_are_shown_as_blocked_while_the_rollout_waits(flow):
    flow.cluster.rollout_lag = 99
    flow.cluster.blocked_reason = "1 pod(s) cannot be scheduled (db-x: 0/3 nodes are available)"
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)

    status = flow.service.status(flow.rec)

    assert status["rollout"]["state"] == "rolling-out" and status["rollout"]["blocked"] is True
    assert "cannot be scheduled" in status["rollout"]["blockedReason"]
    assert status["operation"]["tone"] == "attention" and status["operation"]["nextAction"] == "wait"
    assert status["operation"]["inProgress"] is True and status["canReset"] is True


def test_a_rollout_past_its_deadline_is_timed_out_and_is_watched_again_if_it_recovers(flow):
    flow.cluster.rollout_lag = 99
    change = flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    stored = flow.service.repository.get(change.application_id)
    stored.rollout["deadline"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    flow.service.repository.update(stored)

    late = flow.service.status(flow.rec)

    assert late["rollout"]["state"] == "timed-out" and late["operation"]["nextAction"] == "recheck"
    assert late["analysis"]["apply"]["status"] == "PENDING"

    flow.cluster.rollout_lag = 0
    flow.use_cluster(spread_db())
    recovered = flow.service.status(flow.rec)

    assert recovered["rollout"]["state"] == "completed" and recovered["analysis"]["apply"]["status"] == "COMPLETED"


def test_a_controller_that_cannot_progress_is_reported_failed_with_its_reason(flow):
    flow.cluster.progress_failed = "ProgressDeadlineExceeded"
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)

    status = flow.service.status(flow.rec)

    assert status["rollout"]["state"] == "failed" and "ProgressDeadlineExceeded" in status["operation"]["headline"]
    assert status["canReset"] is True and status["analysis"]["apply"]["status"] == "PENDING"


def test_a_lost_connection_changes_nothing_that_was_stored_and_is_said_plainly(flow):
    flow.cluster.rollout_lag = 99
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.cluster.unreachable = True

    lost = flow.service.status(flow.rec)

    assert lost["operation"]["connectionLost"] is True and lost["rolloutComplete"] is None
    assert lost["rollout"]["state"] == "rolling-out" and "Could not reach the connected cluster" in lost["operation"]["headline"]
    assert lost["operation"]["connection"]["code"] == "CLUSTER_CONNECTION_FAILED"
    assert lost["application"]["status"] == "APPLIED"

    flow.cluster.unreachable = False
    flow.cluster.rollout_lag = 0
    flow.use_cluster(spread_db())
    assert flow.service.status(flow.rec)["rollout"]["state"] == "completed"


def test_a_deployment_deleted_and_recreated_is_not_the_one_that_was_changed(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.cluster.replace_deployment("db")

    status = flow.service.status(flow.rec)

    assert status["rollout"]["state"] == "failed" and "deleted and recreated" in status["rollout"]["deployments"][0]["reason"]
    with pytest.raises(ApplicationRefused) as refused:
        flow.service.reset(flow.rec, "asesh")
    assert refused.value.code == "DEPLOYMENT_REPLACED" and flow.cluster.restores == []


def test_rollout_states_from_status_alone():
    from app.integrations.kubernetes.validation_gateway import DeploymentRollout

    class Reader:
        def __init__(self, **fields):
            self.fields = fields

        def read_rollout(self, namespace, names):
            return {n: DeploymentRollout(n, **self.fields) for n in names}

    record = rollout.fresh("apply", "shop", ["db"], uids={"db": "u1"})
    settled = dict(generation=2, observed_generation=2, desired=3, updated=3, total=3, ready=3, available=3, uid="u1")

    assert rollout.observe(None, Reader(**settled))[0]["state"] == "unknown"
    assert rollout.observe(record, Reader(**{**settled, "observed_generation": 1}))[0]["state"] == "rolling-out"
    assert rollout.observe(record, Reader(**{**settled, "ready": 2}))[0]["state"] == "rolling-out"
    assert rollout.observe(record, Reader(**{**settled, "total": None}))[0]["state"] == "rolling-out"     # old pods not ruled out
    done, event = rollout.observe(record, Reader(**settled))
    assert done["state"] == "completed" and event == "rollout-completed" and done["completedAt"]
    assert rollout.observe(done, Reader(**{**settled, "ready": 0}))[0] is done                 # completed is read back, not re-watched
    assert rollout.observe(record, Reader(**{**settled, "uid": "u2"}))[0]["state"] == "failed"
    missing = Reader(generation=None, observed_generation=None, desired=0, updated=None, total=None,
                                             ready=None, available=None, missing=True)
    assert rollout.observe(record, missing)[0]["state"] == "failed"


def test_unschedulable_pods_are_found_by_the_deployments_selector_only():
    from app.integrations.kubernetes.validation_gateway import _unschedulable

    def stuck(name, labels, reason="Unschedulable"):
        return {"metadata": {"name": name, "labels": labels},
                "status": {"conditions": [{"type": "PodScheduled", "status": "False", "reason": reason, "message": "0/3 nodes"}]}}

    pods = [stuck("db-a", {"app": "db"}), stuck("web-a", {"app": "web"}), stuck("db-b", {"app": "db"}, reason="Other")]

    assert _unschedulable(pods, {"app": "db"}).startswith("1 pod(s) cannot be scheduled (db-a: 0/3 nodes")
    assert _unschedulable(pods, {"app": "cache"}) is None and _unschedulable(pods, {}) is None


# --- reset -------------------------------------------------------------------------------------------------------------------


def test_reset_is_a_second_operation_with_its_own_rollout_restoration_check_and_analysis(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.use_cluster(spread_db())
    applied = flow.service.status(flow.rec)
    flow.cluster.rollout_lag = 2          # the status read below and the apply's own re-check both still see it lagging

    reset = flow.service.reset(flow.rec, "asesh")
    waiting = flow.service.status(flow.rec)

    assert reset.status == RESET and reset.rollout["direction"] == "reset" and reset.rollout["state"] == "rolling-out"
    assert waiting["rolloutComplete"] is False and waiting["operation"]["direction"] == "reset"
    assert waiting["operation"]["stage"] == "rollout" and waiting["canApply"] is False
    assert "still rolling out" in waiting["reason"]
    with pytest.raises(ApplicationRefused) as busy:
        flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    assert busy.value.code == "RESET_ROLLOUT_IN_PROGRESS"
    assert waiting["analysis"]["apply"] == applied["analysis"]["apply"]               # the apply's record is preserved as it was

    flow.use_cluster(concentrated_db())
    flow.service.status(flow.rec)
    restored = flow.service.status(flow.rec)

    assert restored["rollout"]["state"] == "completed" and restored["rollout"]["restoration"]["verified"] is True
    reset_analysis = restored["analysis"]["reset"]
    assert reset_analysis["status"] == "COMPLETED" and reset_analysis["baseline"] == applied["analysis"]["apply"]["baseline"]
    assert reset_analysis["predicted"]["kind"] == "restoration-target"
    assert reset_analysis["comparison"]["direction"] == "unchanged" and reset_analysis["comparison"]["afterBlastRadius"] == 100.0
    assert restored["operation"]["headline"].startswith("Reset complete") and restored["canApply"] is True


def test_a_reset_that_does_not_match_the_stored_state_is_not_called_restored(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.service.reset(flow.rec, "asesh")
    flow.cluster.deployments["db"] = replace(flow.cluster.deployments["db"], replicas=4)     # someone else scaled it since
    flow.use_cluster(concentrated_db())

    status = flow.service.status(flow.rec)

    assert status["rollout"]["restoration"]["verified"] is False
    assert "4 replicas, stored 3" in status["rollout"]["restoration"]["differences"][0]
    assert status["operation"]["tone"] == "attention" and "do not match the stored state" in status["operation"]["headline"]


def test_a_reset_leaves_what_it_did_not_change_as_it_found_it(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.cluster.deployments["db"] = replace(flow.cluster.deployments["db"], pod_labels={"app": "db", "team": "x"})

    flow.service.reset(flow.rec, "asesh")

    assert flow.cluster.deployments["db"].pod_labels == {"app": "db", "team": "x"}
    assert flow.cluster.deployments["web"].replicas == 2 and not flow.cluster.deployments["db"].has_hostname_spread


def test_a_failed_reset_keeps_the_snapshot_and_can_be_tried_again(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.cluster.fail_restore = True

    with pytest.raises(ApplicationRefused):
        flow.service.reset(flow.rec, "asesh")
    failed = flow.service.status(flow.rec)

    assert failed["application"]["status"] == "RESET_FAILED" and failed["application"]["before"][0]["name"] == "db"
    assert failed["canReset"] is True and failed["operation"]["nextAction"] == "reset"
    flow.cluster.fail_restore = False
    flow.service.reset(flow.rec, "asesh")
    assert flow.service.status(flow.rec)["application"]["status"] == "RESET"


def test_resetting_before_the_rollout_was_analysed_closes_that_analysis_with_its_reason(flow):
    flow.cluster.rollout_lag = 99
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)

    flow.service.reset(flow.rec, "asesh")
    status = flow.service.status(flow.rec)

    assert status["analysis"]["apply"]["status"] == "UNAVAILABLE" and status["analysis"]["apply"]["error"]["code"] == "SUPERSEDED_BY_RESET"
    assert status["analysis"]["reset"]["status"] == "PENDING"


def test_a_failed_apply_is_put_back_and_its_analysis_says_there_is_nothing_to_compare(flow):
    flow.cluster.moved_since_read = "db"

    with pytest.raises(ApplicationRefused):
        flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    status = flow.service.status(flow.rec)

    assert status["application"]["status"] == "FAILED" and status["analysis"]["apply"]["status"] == "UNAVAILABLE"
    assert status["analysis"]["apply"]["error"]["code"] == "APPLY_FAILED"


# --- duplicates, concurrency, restart -----------------------------------------------------------------------------------------


def test_two_applications_at_the_same_moment_apply_once(flow, session):
    url = str(session.get_bind().url)
    original = flow.cluster.patch_deployment

    def slow(document, *, expect_resource_version):
        time.sleep(0.4)           # a window wide enough for the second request to arrive while the first is writing
        original(document, expect_resource_version=expect_resource_version)

    flow.cluster.patch_deployment = slow
    outcomes = []

    def attempt():
        with Session(get_engine(url)) as db:
            try:
                outcomes.append(flow.make(db).apply(flow.rec, "asesh", evaluate=evaluate).status)
            except ApplicationRefused as error:
                outcomes.append(error.code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["APPLICATION_ACTIVE", "APPLIED"] and len(flow.cluster.patches) == 1


def test_progress_survives_a_restart_and_nothing_is_applied_again(flow, session):
    flow.cluster.rollout_lag = 1
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    patches = len(flow.cluster.patches)

    with Session(get_engine(str(session.get_bind().url))) as fresh:         # a new process: new session, new service
        restarted = flow.make(fresh)
        still = restarted.status(flow.rec)
        assert still["rollout"]["state"] == "rolling-out" and still["application"]["status"] == "APPLIED"
        flow.use_cluster(spread_db())
        done = restarted.status(flow.rec)

    assert done["rollout"]["state"] == "completed" and done["analysis"]["apply"]["status"] == "COMPLETED"
    assert len(flow.cluster.patches) == patches
    with pytest.raises(ApplicationRefused) as again:
        restarted.apply(flow.rec, "asesh", evaluate=evaluate)
    assert again.value.code == "APPLICATION_ACTIVE"


def test_the_persisted_events_show_each_step_as_it_happened(flow):
    change = flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    status = flow.service.status(flow.rec)

    steps = {s["id"]: s for s in status["operation"]["steps"]}
    assert steps["checking-approval"]["state"] == "done" and steps["checking-approval"]["at"]
    assert steps["saving-snapshot"]["state"] == "done" and steps["submitting-changes"]["state"] == "done"
    assert steps["waiting-for-rollout"]["state"] in ("done", "active")
    assert [e["event"] for e in change.events][:3] == ["approval-verified", "snapshot-saved", "applying"]


def test_a_change_that_predates_rollout_tracking_is_unknown_not_assumed_complete(flow):
    change = flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    stored = flow.service.repository.get(change.application_id)
    stored.rollout = None
    flow.service.repository.update(stored)

    status = flow.service.status(flow.rec)

    assert status["rolloutComplete"] is None and status["operation"]["tone"] == "attention"
    assert "unknown" in status["operation"]["headline"] and status["canReset"] is True


# --- genuine experiments are shown apart from the recalculation --------------------------------------------------------------------


def _measurement(recommendation_id, **over):
    fields = dict(experiment_id="exp-1", recommendation_id=recommendation_id, status="COMPLETED", validated=True, prediction_error=0.0,
                  provenance={"workflowBasis": "DECLARED"}, finished_at=datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc))
    return SimpleNamespace(**{**fields, **over})


def test_only_a_controlled_experiment_for_this_recommendation_is_listed_and_the_recalculation_is_not_one(flow):
    store = SimpleNamespace(list_measurements=lambda: [_measurement(flow.rec), _measurement("rec-other", experiment_id="exp-2")])
    service = flow.make()
    service.experiments = store
    service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.use_cluster(spread_db())

    status = service.status(flow.rec)

    assert [m["experimentId"] for m in status["experiments"]["measurements"]] == ["exp-1"]
    assert status["experiments"]["measurements"][0]["workflowBasis"] == "DECLARED"
    assert status["analysis"]["apply"]["status"] == "COMPLETED" and status["states"]["experimentallyValidated"] is False
    assert "not an experiment" in status["experiments"]["note"]


def test_an_experiment_store_that_cannot_be_read_is_unknown_not_none(flow):
    def broken():
        raise RuntimeError("database is locked")

    service = flow.make()
    service.experiments = SimpleNamespace(list_measurements=broken)

    status = service.status(flow.rec)

    assert status["experiments"]["available"] is False and status["experiments"]["measurements"] == []


def test_without_a_store_no_experiment_is_claimed(flow):
    assert flow.service.status(flow.rec)["experiments"] == {"available": False, "measurements": [], "note": flow.service.status(flow.rec)["experiments"]["note"]}


def test_an_expired_credential_is_said_to_be_one_not_a_lost_connection(flow):
    flow.cluster.rollout_lag = 99
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    flow.cluster.credential_rejected = True

    status = flow.service.status(flow.rec)

    assert status["operation"]["connection"]["code"] == "CLUSTER_AUTH_FAILED"
    assert "rejected the stored credential" in status["operation"]["headline"]
    assert status["rollout"]["state"] == "rolling-out" and status["canReset"] is True
    with pytest.raises(ApplicationRefused) as refused:
        flow.service.retry_analysis(flow.rec)
    assert refused.value.code == "CLUSTER_AUTH_FAILED" and refused.value.retryable is False


def test_an_apply_the_backend_died_in_is_reported_interrupted_and_can_be_reset(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    stored = flow.service.repository.latest()
    stored.status = "APPLYING"                    # what a crash between the first write and the last leaves behind
    flow.service.repository.update(stored)

    status = flow.service.status(flow.rec)

    assert status["operation"]["interrupted"] is True and status["operation"]["tone"] == "attention"
    assert status["operation"]["nextAction"] == "reset" and "interrupted" in status["operation"]["headline"]
    assert status["canReset"] is True and status["operation"]["inProgress"] is False


def test_an_apply_that_is_really_running_is_not_called_interrupted(flow):
    flow.service.apply(flow.rec, "asesh", evaluate=evaluate)
    stored = flow.service.repository.latest()
    stored.status = "APPLYING"
    flow.service.repository.update(stored)

    from app.engine.recommendation_evaluation_validation.application import service as module
    with module._MUTATION:                        # the lock an apply in this process holds from its checks to its last patch
        status = flow.service.status(flow.rec)

    assert status["operation"]["interrupted"] is False and status["operation"]["headline"] == "Applying change…"


# --- the tier adapters fail explicitly, they do not substitute ----------------------------------------------------------------------


def _basis(workflow_basis, **over):
    return {"workflowId": "checkout", "namespace": "shop", "failedNode": "w1", "targetServices": ["db"], "workflowFreezeId": "f1",
            "declarationDigest": None, "workflowBasis": workflow_basis, **over}


def test_the_production_analyser_without_a_frozen_workflow_says_so_with_a_code(use_cluster, monkeypatch):
    from app.engine.workflow_dependency_modeling import service as workflow_service
    from app.engine.recommendation_evaluation_validation.application.live_analyser import ProductionAnalyser

    use_cluster(spread_db())

    def none_frozen(_workflow_id):
        raise LookupError("no frozen workflow checkout")

    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", none_frozen)

    with pytest.raises(EvidenceError) as refused:
        ProductionAnalyser().recalculate(_basis("FROZEN"))

    assert refused.value.code == "WORKFLOW_EVIDENCE_UNAVAILABLE"


def test_the_production_analyser_refuses_a_workflow_that_was_frozen_again(use_cluster, monkeypatch):
    from app.engine.workflow_dependency_modeling import service as workflow_service
    from app.engine.recommendation_evaluation_validation.application.analysis import AnalysisUnavailable
    from app.engine.recommendation_evaluation_validation.application.live_analyser import ProductionAnalyser
    from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph

    use_cluster(spread_db())
    graph = WorkflowGraph(workflow_id="checkout", criticality=0.9, mandatory_services=["web", "db"], edges=[WorkflowEdge(source="web", target="db")])
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", lambda _id: SimpleNamespace(graph=graph, freeze_id="f2", assessment_id="a"))

    with pytest.raises(AnalysisUnavailable) as refused:
        ProductionAnalyser().recalculate(_basis("FROZEN"))
    assert refused.value.code == "WORKFLOW_CHANGED"

    observed = ProductionAnalyser().recalculate(_basis("FROZEN", workflowFreezeId="f2"))        # the same freeze: it is computed, by Component 3
    assert observed["blastRadius"] == 0.0 and observed["workflowFreezeId"] == "f2"


def test_the_research_analyser_with_an_unreadable_cluster_fails_with_a_code_and_invents_no_topology(use_cluster, declared):
    from app.engine.recommendation_evaluation_validation.application.live_analyser import ResearchAnalyser
    from app.shared.evidence_errors import EvidenceError as Coded

    class Unreadable:
        def __getattr__(self, name):
            raise Coded("CLUSTER_NOT_CONNECTED", "No Kubernetes cluster is connected.", http_status=503, component="deployments")

    use_cluster(Unreadable())

    with pytest.raises(Coded) as refused:
        ResearchAnalyser().recalculate(_basis("DECLARED"))

    assert refused.value.code == "DISCOVERY_FAILED"            # Component 2's own coded refusal, passed through unchanged


def test_the_research_analyser_refuses_a_topology_too_incomplete_for_the_workflow(use_cluster, declared):
    from app.engine.recommendation_evaluation_validation.application.live_analyser import ResearchAnalyser

    use_cluster(FakeClusterSource(nodes=[node("w1")], pods={"shop": [pod("web-1", "w1", app="web")]},
                                  deployments={"shop": [deployment("web", 1)]}, template_specs={"web": REQUESTS}))      # db is not there

    with pytest.raises(EvidenceError) as refused:
        ResearchAnalyser().recalculate(_basis("DECLARED"))

    assert refused.value.code == "TOPOLOGY_INCOMPLETE"
