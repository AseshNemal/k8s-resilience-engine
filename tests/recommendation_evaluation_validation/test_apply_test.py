"""Apply and test orchestration against a real migrated DB and fake cluster I/O. No live cluster writes."""
import json
import threading
from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.recommendations import get_apply_test_service, get_research_application_service, get_application_service
from app.core.database import get_engine
from app.main import app
from app.engine.recommendation_evaluation_validation.application import ApplicationRefused
from app.engine.recommendation_evaluation_validation.application.live_analyser import ResearchAnalyser, ProductionAnalyser
from app.engine.recommendation_evaluation_validation.apply_test.measurements import measure_workflows
from app.engine.recommendation_evaluation_validation.apply_test.repository import ApplyTestRun, now
from app.engine.recommendation_evaluation_validation.apply_test.service import ApplyTestService, _sampled_pod_usage, _risk_target_met
from app.engine.recommendation_evaluation_validation.portfolio_pipeline import build_portfolio_recommendation
from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from app.integrations.kubernetes.validation_gateway import ObservedDeployment, KubernetesTestClusterGateway
from app.integrations.kubernetes.client import ApiFailedError
from app.integrations.probe.http_fetch import Fetched
from tests.recommendation_evaluation_validation.test_approval_api import client, override_get_db, sqlite_url  # noqa: F401
from tests.recommendation_evaluation_validation.test_portfolio_recommendation import two_problem_cluster, declared_file
from tests.support.member4_fake_apply_gateway import FakeApplyGateway


@pytest.fixture
def world(use_cluster, tmp_path, monkeypatch, sqlite_url):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps({"workflowChecks": {name: {"url": "http://localhost:30080/productpage", "mustContain": ["working"]}
                                                       for name in ("checkout", "browse")}}))
    monkeypatch.setenv("RESILIENCE_WORKFLOW_PROBES_PATH", str(checks))
    monkeypatch.setenv("RESILIENCE_ALLOW_CLUSTER_WRITES", "1")
    gateway = FakeApplyGateway([ObservedDeployment(name, "shop", str(i + 10), 1, replicas, {"app": name}, {"app": name})
        for i, (name, replicas) in enumerate((("db", 3), ("cache", 2), ("web", 2)))])
    gateway.read_resource_usage = lambda ns: {"available": True, "source": "test-double", "nodes": [], "pods": []}
    engine = get_engine(sqlite_url)
    def application(db, declared):
        return (get_research_application_service(db, lambda: gateway, ResearchAnalyser()) if declared
                else get_application_service(db, lambda: gateway, ProductionAnalyser()))
    measure = lambda live, **kw: measure_workflows(live, fetch=lambda spec: Fetched(200, "working", 2.5, None), **kw)
    service = ApplyTestService(engine, lambda: gateway, build_portfolio_recommendation, application,
        writes_enabled=True, sleep=lambda _: None, measure=measure, rollout_timeout=0)
    live = build_portfolio_recommendation("shop", declared=True)
    candidate = live.report.selected_recommendation_id
    proposal = build_yaml_proposal(live, candidate)
    selection = dict(candidate_id=candidate, namespace="shop", declared=True, fingerprint=proposal.fingerprint,
                     yaml_sha256=proposal.sha256, request_key="explicit-click-123")
    return service, gateway, selection


def test_apply_runs_rollout_workflow_checks_resource_measurements_and_a_persisted_report(world):
    service, gateway, selection = world
    run = service.start(**selection, background=False)
    assert run["status"] == "COMPLETED", run
    assert {p["metadata"]["name"] for p in gateway.patches} == {"db", "cache"}
    assert [e["phase"] for e in run["events"]].index("applying") < [e["phase"] for e in run["events"]].index("testing")
    assert all(r["httpStatus"] == "PASSED" and len(r["samples"]) == 3 for r in run["report"]["after"]["workflows"]["rows"])
    assert run["report"]["faultInjected"] is False
    assert run["report"]["riskAnalysis"]["status"] == "COMPLETED"
    assert run["canReset"]
    assert service.read(run["runId"])["report"] == run["report"]
    with Session(service.engine) as db:
        reset = service.application_factory(db, True).reset(selection["candidate_id"], "Reset click")
    assert reset.status == "RESET" and set(gateway.restores) == {"db", "cache"}


def test_background_run_owns_its_sessions_and_continues_after_the_start_request_returns(world):
    from app.engine.recommendation_evaluation_validation.apply_test.service import _THREADS
    service, gateway, selection = world
    entered, release = threading.Event(), threading.Event()
    measure = service.measure
    def paused(live, **kw):
        entered.set()
        assert release.wait(10)
        return measure(live, **kw)
    service.measure = paused
    run = service.start(**selection)
    assert entered.wait(5)
    worker = _THREADS[run["runId"]]
    assert run["status"] in ("QUEUED", "RUNNING") and not gateway.patches
    release.set()
    worker.join(10)
    assert not worker.is_alive()
    assert service.read(run["runId"])["status"] == "COMPLETED"


def test_multiple_candidates_and_full_reports_survive_reset_and_history_pagination(world, client):
    service, gateway, selection = world
    first = service.start(**selection, background=False)
    with Session(service.engine) as db:
        assert service.application_factory(db, True).reset(selection["candidate_id"], "Reset click").status == "RESET"
    live = build_portfolio_recommendation("shop", declared=True)
    alternative = next(c for c in live.report.recommendations
                       if c.id != selection["candidate_id"] and build_yaml_proposal(live, c.id).status == "GENERATED")
    proposal = build_yaml_proposal(live, alternative.id)
    second = service.start(**{**selection, "candidate_id": alternative.id, "fingerprint": proposal.fingerprint,
        "yaml_sha256": proposal.sha256, "request_key": "another-candidate-click"}, background=False)
    assert second["status"] == "COMPLETED"
    assert service.read(first["runId"])["report"] == first["report"]
    first_page = service.history(limit=1)
    assert first_page["runs"][0]["runId"] == second["runId"] and first_page["hasMore"]
    assert service.history(limit=1, offset=1)["runs"][0]["runId"] == first["runId"]
    assert first_page["runs"][0]["candidate"]["id"] == alternative.id
    assert first_page["runs"][0]["yamlSha256"] == second["report"]["yamlSha256"]
    patches = len(gateway.patches)
    service.loader = lambda *args, **kwargs: pytest.fail("History must not recompute or apply a candidate")
    app.dependency_overrides[get_apply_test_service] = lambda: service
    try:
        history = client.get("/api/recommendations/apply-tests/history?limit=1")
        assert history.status_code == 200 and history.json()["runs"][0]["runId"] == second["runId"]
        assert client.get("/api/recommendations/apply-tests/history?limit=0").status_code == 422
        assert client.get(f"/api/recommendations/apply-tests/{first['runId']}/report").json()["report"] == first["report"]
        assert len(gateway.patches) == patches
    finally:
        app.dependency_overrides.pop(get_apply_test_service, None)


def test_connection_switch_during_baseline_stops_before_patching_the_new_cluster(world):
    service, gateway, selection = world
    measure = service.measure
    def switched(live, **kw):
        result = measure(live, **kw)
        gateway.uid = "another-cluster"
        return result
    service.measure = switched
    run = service.start(**selection, background=False)
    assert run["status"] == "FAILED" and run["report"]["error"]["code"] == "CLUSTER_CHANGED"
    assert not gateway.patches


def test_the_same_candidate_can_be_tested_again_after_reset_without_overwriting_its_first_report(world):
    service, gateway, selection = world
    first = service.start(**selection, background=False)
    with Session(service.engine) as db:
        service.application_factory(db, True).reset(selection["candidate_id"], "Reset click")
    repeated = service.start(**{**selection, "request_key": "new-explicit-retest"}, background=False)
    assert repeated["status"] == "COMPLETED" and repeated["runId"] != first["runId"]
    assert len(service.history()["runs"]) == 2
    assert service.read(first["runId"])["report"] == first["report"]


def test_a_repeated_click_key_never_applies_twice_and_a_reused_key_cannot_change_selection(world):
    service, gateway, selection = world
    first = service.start(**selection, background=False)
    second = service.start(**selection, background=False)
    assert first["runId"] == second["runId"] and len(gateway.patches) == 2
    with pytest.raises(ApplicationRefused, match="different selection"):
        service.start(**{**selection, "fingerprint": "a" * 64}, background=False)


def test_stale_preview_fails_before_any_patch(world):
    service, gateway, selection = world
    run = service.start(**{**selection, "fingerprint": "a" * 64}, background=False)
    assert run["status"] == "FAILED" and not gateway.patches
    assert run["report"]["error"]["code"] == "SELECTION_STALE"


def test_write_arming_cannot_be_bypassed_by_the_new_action(world):
    service, gateway, selection = world
    service.writes_enabled = False
    with pytest.raises(ApplicationRefused) as error:
        service.start(**selection, background=False)
    assert error.value.code == "CLUSTER_WRITES_NOT_ENABLED" and not gateway.patches


def test_missing_http_checks_are_reported_as_gaps_not_passing_workflows(world, monkeypatch):
    service, _, selection = world
    monkeypatch.delenv("RESILIENCE_WORKFLOW_PROBES_PATH")
    run = service.start(**selection, background=False)
    assert run["status"] == "COMPLETED_WITH_GAPS"
    assert all(r["httpStatus"] == "NOT_CONFIGURED" for r in run["report"]["after"]["workflows"]["rows"])


def test_http_200_with_a_dependency_failure_is_a_failed_check(world):
    service, _, selection = world
    service.measure = lambda live, **kw: measure_workflows(live, fetch=lambda spec: Fetched(200, "dependency down", 1, None), **kw)
    run = service.start(**selection, background=False)
    assert run["status"] == "TESTS_FAILED"
    assert any(not s["ok"] for r in run["report"]["after"]["workflows"]["rows"] for s in r["samples"])


def test_rollout_timeout_does_not_run_post_change_checks_and_keeps_reset(world):
    service, gateway, selection = world
    gateway.rollout_lag = 10
    run = service.start(**selection, background=False)
    assert run["status"] == "FAILED" and run["canReset"]
    assert "after" not in run["report"] and run["report"]["error"]["code"] == "ROLLOUT_NOT_COMPLETED"


def test_missing_resource_usage_does_not_become_zero_or_a_complete_report(world):
    service, gateway, selection = world
    gateway.read_resource_usage = lambda ns: {"available": False, "nodes": None, "pods": None}
    run = service.start(**selection, background=False)
    assert run["status"] == "COMPLETED_WITH_GAPS"
    assert run["report"]["after"]["resources"]["nodes"] is None


def test_orphaned_run_is_interrupted_without_repeating_a_write(world):
    service, gateway, selection = world
    service.repo.create("orphan", "orphan-key", {"candidateId": selection["candidate_id"], "events": []})
    with Session(service.engine) as db:
        db.execute(update(ApplyTestRun).where(ApplyTestRun.run_id == "orphan").values(updated_at=now() - timedelta(minutes=5)))
        db.commit()
    assert service.read("orphan")["status"] == "INTERRUPTED"
    assert not gateway.patches


def test_an_active_apply_test_blocks_a_second_run_and_fault_injection(world):
    from app.api.validation_loop import get_application_active
    service, gateway, selection = world
    service.repo.create("already-running", "other-key", {"candidateId": selection["candidate_id"], "events": []})
    assert get_application_active()() is True
    with pytest.raises(ApplicationRefused) as error:
        service.start(**selection, background=False)
    assert error.value.code == "RUN_ACTIVE" and not gateway.patches


def test_apply_test_http_endpoint_has_no_separate_approval_request_and_enforces_preview_binding(client, world):
    service, gateway, selection = world
    original = service.start
    service.start = lambda **kw: original(**kw, background=False)
    app.dependency_overrides[get_apply_test_service] = lambda: service
    try:
        result = client.post("/api/recommendations/apply-tests", json=selection)
        assert result.status_code == 202, result.text
        assert result.json()["status"] == "COMPLETED" and len(gateway.patches) == 2
        run_id = result.json()["runId"]
        assert client.get(f"/api/recommendations/apply-tests/{run_id}/report").status_code == 200
        assert client.get("/api/recommendations/apply-tests/current").json()["run"]["runId"] == run_id
        assert client.post("/api/recommendations/apply-tests", json={"namespace": "shop"}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_apply_test_service, None)


def test_resource_collection_falls_back_to_measured_kubelet_data_without_inventing_zeros():
    gateway = object.__new__(KubernetesTestClusterGateway)
    gateway.identity = lambda: type("Identity", (), {"node_names": ("w1",)})()
    def get(path, what):
        if "metrics.k8s.io" in path:
            raise ApiFailedError(404)
        usage = {"cpu": {"usageNanoCores": 1200, "time": "2026-10-24T10:00:00Z"}, "memory": {"workingSetBytes": 456}}
        return {"node": usage, "pods": [{"podRef": {"namespace": "shop", "name": "db-1"},
            "containers": [{"name": "db", **usage}], **usage}]}
    gateway._get = get
    result = gateway.read_resource_usage("shop")
    assert result["available"] and result["source"] == "kubelet-summary"
    assert result["nodes"][0]["usage"] == {"cpu": "1200n", "memory": 456}
    gateway._get = lambda path, what: (_ for _ in ()).throw(ApiFailedError(404))
    result = gateway.read_resource_usage("shop")
    assert not result["available"] and result["nodes"] is None and result["pods"] is None


def test_history_resource_totals_use_recorded_samples_and_preserve_unknowns():
    snapshot = {"resources": {"pods": [{"containers": [{"usage": {"cpu": "1000000n", "memory": "2Mi"}},
        {"usage": {"cpu": "0n", "memory": 1048576}}]}]}}
    assert _sampled_pod_usage(snapshot) == {"sampledPods": 1, "cpuMillicores": pytest.approx(1), "memoryMiB": pytest.approx(3)}
    snapshot["resources"]["pods"][0]["containers"][0]["usage"]["cpu"] = None
    assert _sampled_pod_usage(snapshot)["cpuMillicores"] is None
    assert _sampled_pod_usage(None)["cpuMillicores"] is None


@pytest.mark.parametrize("predicted,observed,expected", [(0, 100, False), (0, 0, True), (50, 20, True), (None, 0, None), (0, None, None), (False, 0, None)])
def test_model_risk_target_is_separate_from_rollout_or_http_success(predicted, observed, expected):
    assert _risk_target_met({"predictedRisk": predicted, "riskAnalysis": {"observed": {"blastRadius": observed}}}) is expected


def test_earlier_reports_expose_risk_mismatch_without_overwriting_the_saved_evidence(world):
    service, gateway, selection = world
    run = service.start(**selection, background=False)
    report = run["report"]
    report.pop("riskTargetMet", None)
    report["predictedRisk"] = 0
    report["riskAnalysis"]["observed"]["blastRadius"] = 100
    service.repo.update(run["runId"], report=report)
    writes = len(gateway.patches)
    assert service.read(run["runId"])["report"]["riskTargetMet"] is False
    assert service.history()["runs"][0]["riskTargetMet"] is False
    assert "riskTargetMet" not in service.repo.get(run["runId"])["report"]
    assert len(gateway.patches) == writes
