"""The demo control never touches an unverified container or skips recovery."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from app.api import workflows as workflow_api
from app.engine.workflow_dependency_modeling import local_boutique_intervention as demo


class FakeDocker:
    def __init__(self):
        self.state = "running"
        self.actions = []

    def inspect(self, service):
        self.actions.append(("inspect", service))
        return {"Id": "demo-container", "State": {"Status": self.state}}

    def stop(self, service):
        self.actions.append(("stop", service))
        self.state = "exited"

    def start(self, service):
        self.actions.append(("start", service))
        self.state = "running"


def test_docker_identity_accepts_only_repository_compose_files(monkeypatch):
    controller = demo.DockerDemoController()
    labels = {
        "com.docker.compose.service": "cartservice",
        "com.docker.compose.project.config_files": (
            f"{demo._COMPOSE_FILE},{demo._INSTRUMENTED_OVERRIDE}"
        ),
    }
    container = {
        "Name": "/boutique-cartservice", "Config": {"Labels": labels},
        "State": {"Status": "running"},
    }
    monkeypatch.setattr(controller, "_command", lambda *args: json.dumps(container))
    assert controller.inspect("cartservice")["Name"] == "/boutique-cartservice"
    labels["com.docker.compose.project.config_files"] += ",C:/elsewhere/rogue.yml"
    with pytest.raises(ValueError, match="does not belong"):
        controller.inspect("cartservice")


def _setup(monkeypatch, *, fail_trial=False):
    campaign = SimpleNamespace(
        analysis_session_id="analysis:demo",
        workload_profile="online_boutique_checkout_v0_10_1",
        candidate_services=("paymentservice",),
    )
    analysis = SimpleNamespace(trace_source=SimpleNamespace(
        endpoint="http://localhost:16686", kind=SimpleNamespace(value="jaeger")
    ))
    monkeypatch.setattr(demo, "get_experiment_campaign", lambda _: campaign)
    monkeypatch.setattr(demo, "get_workflow_analysis", lambda _: analysis)
    monkeypatch.setattr(demo, "baseline_readiness", lambda _: {"ready": True})
    events = []
    trials = []

    def status(_):
        state = "planned" if not events else (
            "active" if events[-1] == "activated" else "restored"
        )
        return {"services": [{
            "targetService": "paymentservice", "state": state,
            "evidenceReady": False, "requiredTrials": 3,
            "eligibleTrials": 0, "trialCount": 0,
        }]}

    def record(_, payload, *, actor):
        events.append(payload["eventType"])
        assert payload["evidence"].startswith("Docker inspect:")

    def trial(*args, **kwargs):
        if fail_trial:
            raise RuntimeError("Jaeger unavailable")
        trials.append(kwargs)
        return SimpleNamespace(to_mapping=lambda: {"status": "failed"})

    monkeypatch.setattr(demo, "intervention_status", status)
    monkeypatch.setattr(demo, "record_intervention_event", record)
    monkeypatch.setattr(demo, "run_campaign_intervention_trial", trial)
    return events, trials


def test_local_cohort_stops_one_service_runs_trials_and_restores(monkeypatch):
    events, trials = _setup(monkeypatch)
    docker = FakeDocker()
    result = demo.run_local_boutique_intervention_cohort(
        "campaign:demo", "paymentservice", "http://localhost:8080",
        actor="operator", controller=docker,
    )
    assert events == ["activated", "restored"]
    assert len(trials) == 3
    assert all(
        trial["verified_unavailable_service"] == "paymentservice"
        for trial in trials
    )
    assert len(result["outcomes"]) == 3
    assert docker.state == "running"
    assert ("stop", "paymentservice") in docker.actions
    assert ("start", "paymentservice") in docker.actions


def test_local_cohort_restores_after_trial_failure(monkeypatch):
    events, _ = _setup(monkeypatch, fail_trial=True)
    docker = FakeDocker()
    with pytest.raises(RuntimeError, match="Jaeger unavailable"):
        demo.run_local_boutique_intervention_cohort(
            "campaign:demo", "paymentservice", "http://localhost:8080",
            actor="operator", controller=docker,
        )
    assert docker.state == "running"
    assert events == ["activated", "restored"]


def test_local_cohort_rejects_non_local_application_before_docker(monkeypatch):
    _setup(monkeypatch)
    docker = FakeDocker()
    with pytest.raises(ValueError, match="local demo frontend"):
        demo.run_local_boutique_intervention_cohort(
            "campaign:demo", "paymentservice", "https://production.example",
            actor="operator", controller=docker,
        )
    assert docker.actions == []


def test_local_demo_api_requires_explicit_header_and_loopback(monkeypatch):
    calls = []
    monkeypatch.setattr(
        workflow_api, "run_local_boutique_intervention_cohort",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True},
    )
    payload = {"applicationUrl": "http://localhost:8080"}
    def request(host):
        return Request({"type": "http", "method": "POST", "path": "/",
                        "headers": [], "client": (host, 12345)})

    action = workflow_api.execute_local_boutique_intervention_cohort
    with pytest.raises(HTTPException) as remote:
        action("campaign:demo", "paymentservice", request("198.51.100.10"),
               payload, "operator", "stop-and-restore-one-demo-service")
    assert remote.value.status_code == 403
    with pytest.raises(HTTPException) as bad:
        action("campaign:demo", "paymentservice", request("127.0.0.1"),
               payload, "operator", "no")
    assert bad.value.status_code == 403
    accepted = action("campaign:demo", "paymentservice", request("127.0.0.1"),
                      payload, "operator", "stop-and-restore-one-demo-service")
    assert accepted == {"ok": True}
    assert len(calls) == 1
