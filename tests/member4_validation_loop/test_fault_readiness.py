"""Before any real fault: are the worker containers identifiable and controllable, and does their restart policy allow a valid window?"""

import json

from app.engine.recommendation_evaluation_validation.validation_loop.fault_readiness import assess_workers
from app.integrations.kind.recovery import CommandResult, DockerCli


def docker(containers):
    def run(args):
        name = args[2]
        info = containers.get(name)
        if args[1] != "inspect" or info is None:
            return CommandResult(1, "", "No such container")
        body = {"Config": {"Labels": info["labels"]}, "State": {"Running": info.get("running", True)},
                "HostConfig": {"RestartPolicy": {"Name": info.get("policy", "")}}}
        return CommandResult(0, json.dumps([body]), "")
    return DockerCli(run)


def worker(cluster="desktop", role="worker", policy="on-failure", running=True):
    return {"labels": {"io.x-k8s.kind.cluster": cluster, "io.x-k8s.kind.role": role}, "policy": policy, "running": running}


def test_workers_with_no_restart_policy_have_a_valid_window_and_are_ready():
    readiness = assess_workers(docker({"desktop-worker": worker(policy="no"), "desktop-worker2": worker(policy="")}), "desktop",
                               ["desktop-worker", "desktop-worker2"])

    assert readiness.ready and readiness.window == "OK" and readiness.smoke_only is False


def test_the_kind_default_on_failure_policy_leaves_the_window_unverified_and_limits_the_cluster_to_the_smoke_test():
    readiness = assess_workers(docker({"desktop-worker": worker(), "desktop-worker2": worker()}), "desktop",
                               ["desktop-worker", "desktop-worker2"])

    assert readiness.window == "UNVERIFIED" and readiness.smoke_only is True and readiness.ready is False
    assert "may be restarted" in readiness.workers[0].problem


def test_one_valid_observed_window_lifts_the_limit():
    readiness = assess_workers(docker({"desktop-worker": worker()}), "desktop", ["desktop-worker"], window_verified=True)

    assert readiness.window == "OK" and readiness.ready is True and readiness.smoke_only is False


def test_a_container_that_is_not_a_worker_of_the_cluster_is_not_identifiable():
    readiness = assess_workers(docker({"desktop-worker": worker(role="control-plane")}), "desktop", ["desktop-worker"])
    other = assess_workers(docker({"desktop-worker": worker(cluster="another")}), "desktop", ["desktop-worker"])

    assert readiness.identifiable is False and other.identifiable is False and readiness.ready is False


def test_a_container_that_cannot_be_inspected_makes_the_whole_check_unreadable_and_not_ready():
    readiness = assess_workers(docker({"desktop-worker": worker(policy="no")}), "desktop", ["desktop-worker", "desktop-worker2"])

    assert readiness.window == "UNREADABLE" and readiness.ready is False and readiness.smoke_only is False


def test_a_stopped_container_is_not_controllable_for_a_stop_test():
    readiness = assess_workers(docker({"desktop-worker": worker(running=False, policy="no")}), "desktop", ["desktop-worker"])

    assert readiness.controllable is False and readiness.ready is False


def test_no_workers_is_never_ready():
    assert assess_workers(docker({}), "desktop", []).ready is False
