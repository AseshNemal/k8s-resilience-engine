"""The kind worker fault injector: abrupt, authorized, recorded before it acts, and unable to touch anything but a worker.

``docker`` is a scripted double that records every command; no container is stopped and no process is run.
"""

import ast
import json
from pathlib import Path

import pytest

from app.engine.recommendation_evaluation_validation.validation import STOP_KIND_WORKER, Authorization
from app.engine.recommendation_evaluation_validation.validation.node_fault import NodeFaultKind, require_node_failure
from app.integrations.kind.node_fault import FaultRefused, KindWorkerFaultInjector
from app.integrations.kind.recovery import (
    CommandResult,
    DockerCli,
    main as recovery_main,
    pending_records,
    restore_pending,
)

ROOT = Path(__file__).resolve().parents[2]
CLUSTER = "resilience-validation"
DIGEST = "d" * 64
TARGET = "kind-resilience-validation"


def labels(cluster=CLUSTER, role="worker"):
    return {"io.x-k8s.kind.cluster": cluster, "io.x-k8s.kind.role": role}


class FakeDocker:
    """Answers inspect/kill/start for a few containers and records each command. Anything else is a test failure."""

    def __init__(self, containers=None, *, kill_fails=False, start_fails=False, on_kill=None):
        self.containers = containers or {
            f"{CLUSTER}-control-plane": {"labels": labels(role="control-plane"), "running": True},
            f"{CLUSTER}-worker": {"labels": labels(), "running": True},
            f"{CLUSTER}-worker2": {"labels": labels(), "running": True},
            "desktop-worker": {"labels": labels("desktop"), "running": True},
        }
        self.calls, self.kill_fails, self.start_fails, self.on_kill = [], kill_fails, start_fails, on_kill

    def __call__(self, args):
        self.calls.append(list(args))
        assert args[0] == "docker", args
        verb, name = args[1], args[-1]
        known = self.containers.get(name)
        if verb == "inspect":
            if known is None:
                return CommandResult(1, "", f"Error: No such object: {name}")
            body = [{"Config": {"Labels": known["labels"]}, "State": {"Running": known["running"]}}]
            return CommandResult(0, json.dumps(body), "")
        if verb == "kill":
            if self.on_kill:
                self.on_kill()
            if self.kill_fails:
                return CommandResult(1, "", "Error response from daemon: cannot kill")
            known["running"] = False
            return CommandResult(0, name, "")
        if verb == "start":
            if self.start_fails:
                return CommandResult(1, "", "Error response from daemon: cannot start")
            known["running"] = True
            return CommandResult(0, name, "")
        raise AssertionError(f"a command the injector must never run: {args}")

    def verbs(self):
        return [c[1] for c in self.calls]


def injector(docker, tmp_path, workers=(f"{CLUSTER}-worker", f"{CLUSTER}-worker2")):
    return KindWorkerFaultInjector(CLUSTER, workers, tmp_path / "recovery", runner=docker)


def grant(**overrides):
    values = dict(action=STOP_KIND_WORKER, confirmed_by="asesh", plan_digest=DIGEST, target=TARGET)
    return Authorization(**{**values, **overrides})


def armed(docker, tmp_path, node=f"{CLUSTER}-worker", **grant_overrides):
    fault = injector(docker, tmp_path)
    fault.arm(grant(**grant_overrides), plan_digest=DIGEST, target=TARGET, failed_node=node)
    return fault


def records(tmp_path):
    return [json.loads(p.read_text()) for p in sorted((tmp_path / "recovery").glob("*.json"))]


# --- what it is ---------------------------------------------------------------------------------------------------------------

def test_it_is_a_node_failure_by_the_engines_definition_and_not_a_drain(tmp_path):
    fault = injector(FakeDocker(), tmp_path)

    assert require_node_failure(fault) is fault and fault.kind == NodeFaultKind.NODE_STOP
    assert "SIGKILL" in fault.method and "docker kill" in fault.method and "not a drain" in fault.method


# --- it can only ever name a worker of the designated cluster -----------------------------------------------------------------

@pytest.mark.parametrize("cluster", ["", "desktop", "Docker-Desktop", "docker-desktop"])
def test_the_docker_desktop_cluster_and_an_empty_name_are_refused_at_construction(cluster, tmp_path):
    with pytest.raises(FaultRefused) as raised:
        KindWorkerFaultInjector(cluster, ["x-worker"], tmp_path, runner=FakeDocker())

    assert raised.value.code == "CLUSTER_REFUSED"


@pytest.mark.parametrize("workers", [[f"{CLUSTER}-control-plane"], [f"{CLUSTER}-control-plane2"], ["desktop-worker"],
                                     [f"{CLUSTER}-worker", "other-worker"], [], [f"{CLUSTER}-workerX"], ["$(rm -rf)"]])
def test_only_workers_of_the_designated_cluster_can_be_named(workers, tmp_path):
    with pytest.raises(FaultRefused) as raised:
        KindWorkerFaultInjector(CLUSTER, workers, tmp_path, runner=FakeDocker())

    assert raised.value.code == "NODE_NOT_ALLOWED"


# --- the separate authorization -------------------------------------------------------------------------------------------------

def test_an_injector_that_was_not_armed_does_nothing(tmp_path):
    docker = FakeDocker()

    with pytest.raises(FaultRefused) as raised:
        injector(docker, tmp_path).inject(f"{CLUSTER}-worker")

    assert raised.value.code == "NOT_ARMED" and docker.calls == []


@pytest.mark.parametrize("override", [dict(action="FAIL_NODE_IN_TEST_CLUSTER"), dict(plan_digest="e" * 64),
                                      dict(target="kind-somewhere-else"), dict(confirmed_by="  ")])
def test_an_authorization_for_another_action_plan_target_or_nobody_does_not_arm(override, tmp_path):
    fault = injector(FakeDocker(), tmp_path)

    with pytest.raises(FaultRefused) as raised:
        fault.arm(grant(**override), plan_digest=DIGEST, target=TARGET, failed_node=f"{CLUSTER}-worker")

    assert raised.value.code == "AUTHORIZATION_MISMATCH"


def test_arming_is_for_one_node_and_one_injection(tmp_path):
    docker = FakeDocker()
    fault = armed(docker, tmp_path)

    with pytest.raises(FaultRefused) as raised:
        fault.inject(f"{CLUSTER}-worker2")
    assert raised.value.code == "NOT_ARMED" and "kill" not in docker.verbs()

    fault.inject(f"{CLUSTER}-worker")
    fault.restore(f"{CLUSTER}-worker")
    with pytest.raises(FaultRefused):
        fault.inject(f"{CLUSTER}-worker")           # consumed: a second kill needs a second authorization


def test_a_second_kill_needs_a_second_authorization_of_its_own(tmp_path):
    docker = FakeDocker()
    fault = armed(docker, tmp_path)
    fault.arm(grant(confirmed_by="second-person"), plan_digest=DIGEST, target=TARGET, failed_node=f"{CLUSTER}-worker")

    fault.inject(f"{CLUSTER}-worker"); fault.restore(f"{CLUSTER}-worker")
    fault.inject(f"{CLUSTER}-worker"); fault.restore(f"{CLUSTER}-worker")
    with pytest.raises(FaultRefused) as raised:
        fault.inject(f"{CLUSTER}-worker")

    assert raised.value.code == "NOT_ARMED" and docker.verbs().count("kill") == 2
    assert sorted(r["authorizedBy"] for r in records(tmp_path)) == ["asesh", "second-person"]


# --- checks against the real container before anything is killed -------------------------------------------------------------------

def test_a_container_of_another_kind_cluster_is_never_killed_even_when_the_name_matches(tmp_path):
    docker = FakeDocker()
    docker.containers[f"{CLUSTER}-worker"]["labels"] = labels("desktop")

    with pytest.raises(FaultRefused) as raised:
        armed(docker, tmp_path).inject(f"{CLUSTER}-worker")

    assert raised.value.code == "CONTAINER_MISMATCH" and "kill" not in docker.verbs()


def test_a_control_plane_container_is_never_killed_even_if_it_is_somehow_named(tmp_path):
    docker = FakeDocker()
    docker.containers[f"{CLUSTER}-worker"]["labels"] = labels(role="control-plane")

    with pytest.raises(FaultRefused) as raised:
        armed(docker, tmp_path).inject(f"{CLUSTER}-worker")

    assert raised.value.code == "CONTAINER_MISMATCH" and "kill" not in docker.verbs()


def test_a_container_that_is_missing_or_already_stopped_is_not_killed(tmp_path):
    docker = FakeDocker()
    docker.containers[f"{CLUSTER}-worker"]["running"] = False
    with pytest.raises(FaultRefused) as raised:
        armed(docker, tmp_path).inject(f"{CLUSTER}-worker")
    assert raised.value.code == "CONTAINER_NOT_RUNNING"

    del docker.containers[f"{CLUSTER}-worker"]
    with pytest.raises(FaultRefused) as raised:
        armed(docker, tmp_path).inject(f"{CLUSTER}-worker")
    assert raised.value.code == "CONTAINER_NOT_FOUND" and "kill" not in docker.verbs()


# --- recorded before it acts ---------------------------------------------------------------------------------------------------------

def test_the_recovery_record_is_on_disk_before_the_container_is_killed(tmp_path):
    seen = {}
    docker = FakeDocker(on_kill=lambda: seen.update(records=records(tmp_path)))

    armed(docker, tmp_path).inject(f"{CLUSTER}-worker")

    (before,) = seen["records"]
    assert before["state"] == "INJECTING" and before["container"] == f"{CLUSTER}-worker" and before["cluster"] == CLUSTER
    assert before["authorizedBy"] == "asesh" and before["planDigest"] == DIGEST
    assert before["restoreCommand"] == ["docker", "start", f"{CLUSTER}-worker"]
    (after,) = records(tmp_path)
    assert after["state"] == "STOPPED" and after["stoppedAt"] and docker.containers[f"{CLUSTER}-worker"]["running"] is False


def test_the_kill_is_a_kill_and_never_a_graceful_stop_or_a_removal(tmp_path):
    docker = FakeDocker()
    armed(docker, tmp_path).inject(f"{CLUSTER}-worker")

    assert [c[1] for c in docker.calls] == ["inspect", "kill"] and docker.calls[-1] == ["docker", "kill", f"{CLUSTER}-worker"]


def test_a_failed_kill_leaves_the_record_pending_because_the_node_state_is_unknown(tmp_path):
    docker = FakeDocker(kill_fails=True)

    with pytest.raises(FaultRefused) as raised:
        armed(docker, tmp_path).inject(f"{CLUSTER}-worker")

    assert raised.value.code == "KILL_FAILED"
    assert [r["state"] for r in records(tmp_path)] == ["INJECTING"] and len(pending_records(tmp_path / "recovery")) == 1


def test_an_unrestored_earlier_failure_blocks_the_next_one(tmp_path):
    docker = FakeDocker()
    armed(docker, tmp_path).inject(f"{CLUSTER}-worker")                  # never restored

    with pytest.raises(FaultRefused) as raised:
        armed(docker, tmp_path, node=f"{CLUSTER}-worker2").inject(f"{CLUSTER}-worker2")

    assert raised.value.code == "RECOVERY_PENDING" and docker.verbs().count("kill") == 1


# --- restoring ---------------------------------------------------------------------------------------------------------------------------

def test_restore_starts_the_container_verifies_it_runs_and_closes_the_record(tmp_path):
    docker = FakeDocker()
    fault = armed(docker, tmp_path)
    fault.inject(f"{CLUSTER}-worker")

    fault.restore(f"{CLUSTER}-worker")

    assert docker.containers[f"{CLUSTER}-worker"]["running"] is True
    (record,) = records(tmp_path)
    assert record["state"] == "RESTORED" and record["restoredAt"] and pending_records(tmp_path / "recovery") == []


def test_restoring_a_node_that_was_never_stopped_starts_nothing_and_writes_nothing(tmp_path):
    docker = FakeDocker()

    injector(docker, tmp_path).restore(f"{CLUSTER}-worker")

    assert "start" not in docker.verbs() and records(tmp_path) == []


def test_restore_refuses_to_start_a_container_that_stopped_being_the_designated_clusters_worker(tmp_path):
    docker = FakeDocker()
    fault = armed(docker, tmp_path)
    fault.inject(f"{CLUSTER}-worker")
    docker.containers[f"{CLUSTER}-worker"]["labels"] = labels("desktop")      # the name now points at something else

    with pytest.raises(FaultRefused) as raised:
        fault.restore(f"{CLUSTER}-worker")

    assert raised.value.code == "RESTORE_FAILED" and "start" not in docker.verbs()
    assert records(tmp_path)[0]["state"] == "RESTORE_FAILED"


def test_a_restore_that_fails_says_the_exact_command_to_run_by_hand_and_keeps_the_record_pending(tmp_path):
    docker = FakeDocker()
    fault = armed(docker, tmp_path)
    fault.inject(f"{CLUSTER}-worker")
    docker.start_fails = True

    with pytest.raises(FaultRefused) as raised:
        fault.restore(f"{CLUSTER}-worker")

    assert f"docker start {CLUSTER}-worker" in raised.value.message
    assert records(tmp_path)[0]["state"] == "RESTORE_FAILED" and len(pending_records(tmp_path / "recovery")) == 1


# --- the explicit restoration procedure -----------------------------------------------------------------------------------------------------

def test_the_recovery_procedure_lists_by_default_and_restores_only_when_asked(tmp_path, capsys):
    docker = FakeDocker()
    armed(docker, tmp_path).inject(f"{CLUSTER}-worker")
    folder = str(tmp_path / "recovery")

    assert recovery_main([folder], runner=docker) == 2                      # pending, nothing done
    assert "docker start" in capsys.readouterr().out and docker.containers[f"{CLUSTER}-worker"]["running"] is False

    assert recovery_main([folder, "--restore", "--cluster", CLUSTER], runner=docker) == 0
    assert docker.containers[f"{CLUSTER}-worker"]["running"] is True and pending_records(tmp_path / "recovery") == []


def test_the_procedure_refuses_to_restore_a_record_of_another_cluster(tmp_path):
    docker = FakeDocker()
    armed(docker, tmp_path).inject(f"{CLUSTER}-worker")

    outcome = restore_pending(tmp_path / "recovery", DockerCli(docker), cluster="some-other-cluster")

    assert outcome == {"restored": [], "failed": [], "skipped": [f"{CLUSTER}-worker"]} and "start" not in docker.verbs()


def test_the_docker_wrapper_runs_only_inspect_kill_and_start_on_a_safe_name():
    docker = DockerCli(FakeDocker())

    for verb in ("stop", "rm", "exec", "run", "pause", "restart"):
        with pytest.raises(ValueError):
            docker.run(verb, "x-worker")
    with pytest.raises(ValueError):
        docker.run("kill", "x-worker; rm -rf /")
    with pytest.raises(ValueError):
        docker.run("kill", "--all")


# --- layering ---------------------------------------------------------------------------------------------------------------------------------

def test_the_kind_integration_imports_nothing_from_the_engine_and_never_runs_a_shell():
    for name in ("node_fault.py", "recovery.py"):
        source = (ROOT / "app" / "integrations" / "kind" / name).read_text()
        tree = ast.parse(source)
        modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [m for m in modules if m.startswith("app.engine")], (name, modules)
        assert "shell=True" not in source and "os.system" not in source and "kubectl" not in source
