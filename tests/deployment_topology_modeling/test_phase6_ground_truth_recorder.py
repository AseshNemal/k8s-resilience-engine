"""Ground-truth recorder: what it reads as the observed outcome, and that a stopped node is always started again.

No cluster and no docker: kubectl and docker are recording fakes.
"""
import json

import pytest

from experiments.placement_drift.exceptions import ChaosSafetyError, ScenarioExecutionError
from experiments.placement_drift.live.ground_truth_recorder import (
    GroundTruthRecorder,
    MixedSetup,
    ready_endpoints_on,
    service_availability,
)


def slice_(service, *endpoints):
    return {"metadata": {"labels": {"kubernetes.io/service-name": service}},
            "endpoints": [{"nodeName": node, "conditions": {"ready": ready}} for node, ready in endpoints]}


def test_a_service_is_available_while_one_endpoint_is_ready():
    slices = [slice_("reviews", ("w1", False), ("w2", True)), slice_("details", ("w1", False))]
    assert service_availability(slices) == {"reviews": True, "details": False}


def test_availability_combines_every_slice_of_a_service():
    slices = [slice_("reviews", ("w1", False)), slice_("reviews", ("w3", True))]
    assert service_availability(slices) == {"reviews": True}


def test_ready_endpoints_are_counted_per_node():
    slices = [slice_("reviews", ("w1", True), ("w2", True)), slice_("details", ("w1", False))]
    assert ready_endpoints_on(slices, "w1") == 1


class FakeKube:
    def __init__(self, fail_detection=False):
        self.calls = []
        self.node_ready = {"w1": True, "w2": True}
        self.fail_detection = fail_detection

    def worker_nodes(self):
        return ["w1", "w2"]

    def get_json(self, kind, *args, namespaced=True):
        if kind == "nodes":
            return {"items": [{"metadata": {"name": n}, "status": {"conditions": [
                {"type": "Ready", "status": "True" if ok else "False"}]}} for n, ok in self.node_ready.items()]}
        return {"items": [slice_("reviews", ("w1", False), ("w2", True))]}

    def run(self, *args, namespaced=True):
        self.calls.append(args)
        return json.dumps({"items": []}) if "json" in args else "NAME\n"

    def wait_until_stable(self, deployments, timeout_s=0):
        self.calls.append(("wait_until_stable",))


def make(tmp_path, kube, docker_calls):
    def fake_docker(*args):
        docker_calls.append(args)
        if args[0] == "stop" and not kube.fail_detection:
            kube.node_ready[args[1]] = False
        if args[0] == "start":
            kube.node_ready[args[1]] = True
    return GroundTruthRecorder(kube, ["reviews-v1"], tmp_path, run_docker=fake_docker, poll_s=0, detect_timeout_s=0)


def test_a_node_failure_records_the_observed_outcome_and_starts_the_node_again(tmp_path):
    kube, docker_calls = FakeKube(), []
    outcome = make(tmp_path, kube, docker_calls).fail_node(tmp_path / "w1", "w1")
    assert outcome == {"reviews": True}
    assert (tmp_path / "w1" / "w1-failure" / "service-result.txt").read_text() == "reviews SERVICE_AVAILABLE\n"
    assert docker_calls == [("stop", "w1"), ("start", "w1")]


def test_the_node_is_started_again_even_when_detection_times_out(tmp_path):
    kube, docker_calls = FakeKube(fail_detection=True), []
    with pytest.raises(ScenarioExecutionError):
        make(tmp_path, kube, docker_calls).fail_node(tmp_path / "w1", "w1")
    assert docker_calls[-1] == ("start", "w1")


def test_the_mixed_setup_applies_the_no_rule_config_first_and_uncordons_afterwards(tmp_path):
    calls = []

    class SetupKube:
        def __getattr__(self, name):
            return lambda *args, **kwargs: calls.append((name, *args))

        def worker_nodes(self):
            return ["w1", "w2", "w3"]

    GroundTruthRecorder(SetupKube(), ["reviews-v1"], tmp_path).apply_mixed_setup(
        MixedSetup(manifest="config-a-no-rule.yaml"))

    assert calls[0] == ("apply", "config-a-no-rule.yaml")
    names = [c[0] for c in calls]
    assert names.index("cordon") < names.index("rollout_restart") < names.index("uncordon")
    assert ("uncordon", "w2") in calls and ("uncordon", "w3") in calls
    assert ("cordon", "w1") not in calls  # the one node that takes every reviews replica


def test_only_worker_nodes_are_stopped(tmp_path):
    kube, docker_calls = FakeKube(), []
    with pytest.raises(ChaosSafetyError):
        make(tmp_path, kube, docker_calls).fail_node(tmp_path / "cp", "control-plane")
    assert docker_calls == []
