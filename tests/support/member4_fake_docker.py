"""A scripted ``docker`` for the kind injector tests (TEST-ONLY): inspect, kill and start on a few containers, nothing run."""

import json

from app.integrations.kind.recovery import CommandResult

CLUSTER = "resilience-validation"


def labels(cluster=CLUSTER, role="worker"):
    return {"io.x-k8s.kind.cluster": cluster, "io.x-k8s.kind.role": role}


class FakeDocker:
    """Answers inspect/kill/start for a few containers and records each command. Anything else is a test failure."""

    def __init__(self, containers=None, *, kill_fails=False, start_fails=False, on_kill=None, on_state=None):
        self.containers = containers or {
            f"{CLUSTER}-control-plane": {"labels": labels(role="control-plane"), "running": True},
            f"{CLUSTER}-worker": {"labels": labels(), "running": True},
            f"{CLUSTER}-worker2": {"labels": labels(), "running": True},
            "desktop-worker": {"labels": labels("desktop"), "running": True},
        }
        self.calls, self.kill_fails, self.start_fails, self.on_kill = [], kill_fails, start_fails, on_kill
        self.on_state = on_state      # called with (container, running) after a kill or a start, to couple a fake cluster to it

    def __call__(self, args):
        self.calls.append(list(args))
        assert args[0] == "docker", args
        verb, name = args[1], args[-1]
        known = self.containers.get(name)
        if verb == "inspect":
            if known is None:
                return CommandResult(1, "", f"Error: No such object: {name}")
            body = [{"Config": {"Labels": known["labels"]}, "State": {"Running": known["running"]},
                     "HostConfig": {"RestartPolicy": {"Name": known.get("policy", "no")}}}]
            return CommandResult(0, json.dumps(body), "")
        if verb == "kill":
            if self.on_kill:
                self.on_kill()
            if self.kill_fails:
                return CommandResult(1, "", "Error response from daemon: cannot kill")
            known["running"] = False
            if self.on_state:
                self.on_state(name, False)
            return CommandResult(0, name, "")
        if verb == "start":
            if self.start_fails:
                return CommandResult(1, "", "Error response from daemon: cannot start")
            known["running"] = True
            if self.on_state:
                self.on_state(name, True)
            return CommandResult(0, name, "")
        raise AssertionError(f"a command the injector must never run: {args}")

    def verbs(self):
        return [c[1] for c in self.calls]
