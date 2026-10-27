"""Backend-verified Kubernetes outages for a workflow campaign: order of events, verification, and recovery on every path."""

from dataclasses import replace

import pytest

from app.engine.workflow_dependency_modeling import kubernetes_intervention as k8s
from app.integrations.kubernetes.workflow_outage import DeploymentState, OutageClusterError

CAMPAIGN = "campaign:test"
campaign_state: dict = {}


class FakeScaler:
    """Deployment state that changes only when told to; ``lag`` inspections pass before a change is visible."""

    namespace = "resilience-workflow-demo"

    def __init__(self, lag=0, never_down=False):
        self.state = DeploymentState("emailservice", self.namespace, "uid-1", "7", 1, 1, 1, None)
        self.lag = lag
        self.never_down = never_down
        self.pending = None
        self.log = []

    def require_opt_in(self):
        self.log.append("opt-in")

    def interrupted(self):
        return ["emailservice"] if self.state.original_replicas is not None else []

    def inspect(self, name):
        if self.pending is not None:
            if self.lag:
                self.lag -= 1
            else:
                self.state, self.pending = self.pending, None
        return self.state

    def take_down(self, state):
        self.log.append("take_down")
        recorded = replace(self.state, original_replicas=1, replicas=0)
        self.state = recorded
        self.pending = None if self.never_down else replace(recorded, ready_replicas=0, pod_count=0)
        if self.never_down:
            self.state = replace(recorded, ready_replicas=1, pod_count=1)

    def restore(self, state):
        self.log.append("restore")
        self.state = replace(self.state, original_replicas=None, replicas=1)
        self.pending = replace(self.state, ready_replicas=1, pod_count=1)
        return 1


@pytest.fixture
def campaign(monkeypatch):
    """Campaign functions replaced by a recorder; the intervention state follows the recorded events."""
    events, trials = [], []
    active = {"value": False}
    monkeypatch.setattr(k8s, "_check_cohort_allowed", lambda campaign_id, service: 2)

    def record(campaign_id, data, *, actor):
        events.append((data["eventType"], data["evidence"]))
        active["value"] = data["eventType"] == "activated"

    def trial(campaign_id, service, url, *, actor, verified_unavailable_service):
        trials.append(verified_unavailable_service)

        class Outcome:
            def to_mapping(self):
                return {"service": service}
        return Outcome()

    monkeypatch.setattr(k8s, "record_intervention_event", record)
    monkeypatch.setattr(k8s, "run_campaign_intervention_trial", trial)
    excluded = {"count": 0}  # how many of the first trials are excluded (incomplete traces)

    def progress(campaign_id, service):
        usable = max(0, len(trials) - excluded["count"])
        return {"state": "active" if active["value"] else "restored", "eligibleTrials": usable,
                "requiredTrials": 2, "trialCount": len(trials)}

    monkeypatch.setattr(k8s, "_service_progress", progress)
    campaign_state["excluded"] = excluded
    monkeypatch.setattr(k8s, "intervention_status", lambda campaign_id: {"services": []})
    return events, trials


def run(scaler):
    clock = iter(range(0, 10_000, 2))
    return k8s.run_kubernetes_intervention_cohort(
        CAMPAIGN, "emailservice", "http://host.docker.internal:18080", actor="tester",
        scaler=scaler, sleeper=lambda _seconds: None, clock=lambda: next(clock),
    )


def test_a_cohort_takes_the_service_down_verifies_it_runs_trials_then_restores(campaign):
    events, trials = campaign
    scaler = FakeScaler(lag=2)

    run(scaler)

    assert scaler.log == ["opt-in", "take_down", "restore"]
    assert [kind for kind, _ in events] == ["activated", "restored"]
    assert "specReplicas=0" in events[0][1]
    assert "matchingPods=0" in events[0][1]
    assert "readyReplicas=1" in events[1][1]
    # Every trial carries the backend-verified outage marker for exactly this service.
    assert trials == ["emailservice", "emailservice"]
    assert scaler.state.restored


def test_excluded_runs_are_replaced_until_the_cohort_has_enough_usable_runs(campaign):
    events, trials = campaign
    campaign_state["excluded"]["count"] = 1

    run(FakeScaler())

    assert trials == ["emailservice"] * 3          # one excluded run replaced: 2 usable of 3 attempts
    assert [kind for kind, _ in events] == ["activated", "restored"]


def test_a_service_that_never_goes_down_is_restored_and_no_trial_runs(campaign):
    events, trials = campaign
    scaler = FakeScaler(never_down=True)

    with pytest.raises(OutageClusterError, match="did not go down"):
        run(scaler)

    assert trials == []
    assert [kind for kind, _ in events] == []
    assert scaler.log[-1] == "restore"
    assert scaler.state.restored


def test_a_failing_trial_still_restores_the_service_and_records_recovery(campaign, monkeypatch):
    events, _ = campaign

    def broken(*args, **kwargs):
        raise ConnectionError("application unreachable")
    monkeypatch.setattr(k8s, "run_campaign_intervention_trial", broken)
    scaler = FakeScaler()

    with pytest.raises(ConnectionError):
        run(scaler)

    assert scaler.log[-1] == "restore" and scaler.state.restored
    assert [kind for kind, _ in events] == ["activated", "restored"]


def test_an_unrecovered_earlier_outage_blocks_a_new_one(campaign):
    scaler = FakeScaler()
    scaler.state = replace(scaler.state, original_replicas=1, replicas=0, ready_replicas=0, pod_count=0)
    with pytest.raises(ValueError, match="not recovered"):
        run(scaler)
    assert "take_down" not in scaler.log


def test_recovery_only_restores(campaign, monkeypatch):
    events, _ = campaign
    scaler = FakeScaler()
    scaler.state = replace(scaler.state, original_replicas=1, replicas=0, ready_replicas=0, pod_count=0)
    monkeypatch.setattr(k8s, "_service_progress", lambda campaign_id, service: {"state": "active"})

    result = k8s.recover_kubernetes_outages(CAMPAIGN, actor="tester", scaler=scaler,
                                            sleeper=lambda _s: None, clock=iter(range(0, 10_000, 2)).__next__)

    assert result["recovered"] == ["emailservice"]
    assert scaler.log == ["restore"]
    assert scaler.state.restored
    assert [kind for kind, _ in events] == ["restored"]


def test_writes_must_be_armed_and_a_namespace_configured(monkeypatch):
    monkeypatch.delenv(k8s.WRITES_SETTING, raising=False)
    monkeypatch.setenv(k8s.NAMESPACE_SETTING, "resilience-workflow-demo")
    with pytest.raises(ValueError, match="not enabled"):
        k8s._scaler()
    monkeypatch.setenv(k8s.WRITES_SETTING, "1")
    monkeypatch.delenv(k8s.NAMESPACE_SETTING)
    with pytest.raises(ValueError, match="No outage namespace"):
        k8s._scaler()


def test_while_a_test_runs_its_outage_is_not_offered_for_restore(monkeypatch):
    monkeypatch.setattr(k8s, "cluster_job_running", lambda: True)
    monkeypatch.setattr(k8s, "get_experiment_campaign", lambda campaign_id: object())
    status = k8s.kubernetes_outage_status(CAMPAIGN)
    assert status["busy"] is True
    assert status["interrupted"] == []
    assert not status["available"]
    scaler = FakeScaler()
    with pytest.raises(ValueError, match="intentional"):
        k8s.recover_kubernetes_outages(CAMPAIGN, actor="tester", scaler=scaler)
    assert scaler.log == []
