"""Repeating an experiment copies only reviewed inputs and runs baseline, outages and assessment in order."""

from types import SimpleNamespace

import pytest

from app.engine.workflow_dependency_modeling import experiment_pipeline as pipeline


@pytest.fixture
def world(monkeypatch):
    calls = []
    source = SimpleNamespace(
        execution_definition=SimpleNamespace(to_mapping=lambda: {"steps": ["reviewed"]}),
        analysis_session_id="analysis:old", workflow_name="Checkout", success_contract=SimpleNamespace(outcome="Order placed"),
        require_criticality=lambda: 0.9)
    analysis = SimpleNamespace(environment_id="env", evidence_tier=SimpleNamespace(value="experimental"),
                               trace_source=SimpleNamespace(to_mapping=lambda: {"type": "jaeger", "endpoint": "http://j"}),
                               entry_service="web", operation_name="POST /checkout")
    baseline = {"eligible": 0}

    def readiness(campaign):
        return {"ready": baseline["eligible"] >= 3, "eligibleTrials": baseline["eligible"], "requiredTrials": 3,
                "recordedTrials": baseline["eligible"], "blockers": []}

    def baseline_run(campaign_id, url, *, actor):
        calls.append(("baseline", url))
        baseline["eligible"] += 1

    monkeypatch.setattr(pipeline, "cluster_job_running", lambda: False)
    monkeypatch.setattr(pipeline, "get_experiment_campaign", lambda campaign_id: source)
    monkeypatch.setattr(pipeline, "outage_scaler", lambda: SimpleNamespace(require_opt_in=lambda: None))
    monkeypatch.setattr(pipeline, "get_workflow_analysis", lambda session_id: analysis)
    monkeypatch.setattr(pipeline, "start_workflow_analysis", lambda data, actor: calls.append(("inspect", data)) or SimpleNamespace(session_id="analysis:new"))
    monkeypatch.setattr(pipeline, "create_experiment_campaign", lambda data, actor: calls.append(("create", data)) or SimpleNamespace(campaign_id="campaign:new", to_mapping=lambda: {"campaignId": "campaign:new"}))
    monkeypatch.setattr(pipeline, "create_experiment_manifest", lambda campaign_id, controls, actor: calls.append(("manifest", campaign_id)))
    monkeypatch.setattr(pipeline, "baseline_readiness", readiness)
    monkeypatch.setattr(pipeline, "run_campaign_baseline_trial", baseline_run)
    monkeypatch.setattr(pipeline, "intervention_status", lambda campaign_id: {"services": [
        {"targetService": "ads", "evidenceReady": True}, {"targetService": "pay", "evidenceReady": True}]})
    monkeypatch.setattr(pipeline, "run_kubernetes_intervention_cohort", lambda campaign_id, service, url, *, actor: calls.append(("outage", service)))
    monkeypatch.setattr(pipeline, "assess_experiment_campaign", lambda campaign_id, *, actor: calls.append(("assess", campaign_id)))

    def run_now(key, info, work):
        job = dict(info, state="running")
        work(job)
        return job

    monkeypatch.setattr(pipeline, "start_cluster_job", run_now)
    return calls


def test_a_repeat_copies_the_reviewed_inputs_and_runs_every_stage_in_order(world):
    result = pipeline.repeat_experiment("campaign:old", actor="researcher", required_trials=3, application_url="http://app.test")
    inspect = next(data for kind, data in world if kind == "inspect")
    create = next(data for kind, data in world if kind == "create")
    assert inspect["operationName"] == "POST /checkout" and inspect["lookback"] == "24h"
    assert create == {"analysisSessionId": "analysis:new", "successContractConfirmed": True, "requiredBaselineTrials": 3,
                      "criticality": 0.9, "workflowName": "Checkout", "businessOutcome": "Order placed",
                      "executionDefinition": {"steps": ["reviewed"]}}
    stages = [kind for kind, _ in world]
    assert stages == ["inspect", "create", "manifest", "baseline", "baseline", "baseline", "outage", "outage", "assess"]
    assert result["job"]["phase"] == "done"


def test_a_repeat_waits_for_any_running_cluster_job(world, monkeypatch):
    monkeypatch.setattr(pipeline, "cluster_job_running", lambda: True)
    with pytest.raises(ValueError, match="already|running"):
        pipeline.repeat_experiment("campaign:old", actor="researcher", required_trials=3, application_url="http://app.test")
    assert world == []
