"""Durable recovery records for a stopped kind worker, a restricted ``docker`` wrapper, and the restoration procedure.

Why this exists. A validation run stops a worker node on purpose. If the process dies between "stopped" and "started again"
(a crash, a closed laptop, a lost terminal), the only thing that remembers the node is down is whatever was written down
BEFORE it was stopped. So the injector writes one small JSON file per stop, to a directory the caller names, before it acts,
and updates it as the stop and the restore happen. A file whose state is not ``RESTORED`` is a pending recovery: it blocks
the next injection, and this module's command line lists it and, when asked, restores it.

    python -m app.integrations.kind.recovery <directory>                         list pending recoveries (changes nothing)
    python -m app.integrations.kind.recovery <directory> --restore --cluster C   start the stopped workers of kind cluster C

The wrapper. Everything that touches Docker goes through ``DockerCli``, which runs ``docker`` with an argument list (never a
shell) and only the verbs ``inspect``, ``kill`` and ``start``, on a container name that matches a strict pattern. ``stop``,
``rm``, ``exec``, ``run`` and every option are refused before a process is started.

Nothing here knows about the engine.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

DOCKER_VERBS = frozenset({"inspect", "kill", "start"})
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
KIND_CLUSTER_LABEL = "io.x-k8s.kind.cluster"
KIND_ROLE_LABEL = "io.x-k8s.kind.role"
RESTORED = "RESTORED"


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], CommandResult]


def subprocess_runner(args: Sequence[str]) -> CommandResult:
    """Run ``args`` (an argument list, never a shell string) and return what it printed."""
    completed = subprocess.run(list(args), capture_output=True, text=True, timeout=60, check=False)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class DockerCli:
    def __init__(self, runner: Runner = subprocess_runner) -> None:
        self._runner = runner

    def run(self, verb: str, name: str) -> CommandResult:
        if verb not in DOCKER_VERBS:
            raise ValueError(f"docker {verb!r} is not allowed here; only {', '.join(sorted(DOCKER_VERBS))}")
        if not SAFE_NAME.match(name or ""):
            raise ValueError(f"{name!r} is not a plain container name")
        return self._runner(["docker", verb, name])

    def inspect(self, name: str) -> dict[str, Any] | None:
        """``{"labels": {...}, "running": bool}`` for the container, or None when there is no such container."""
        result = self.run("inspect", name)
        if result.returncode != 0:
            return None
        try:
            body = json.loads(result.stdout)[0]
            return {"labels": dict(body["Config"].get("Labels") or {}), "running": bool(body["State"]["Running"])}
        except (ValueError, KeyError, IndexError, TypeError):
            return None

    def restart_policy(self, name: str) -> str | None:
        """The container's Docker restart policy name ("no", "on-failure", "always", "unless-stopped"), or None when unreadable."""
        result = self.run("inspect", name)
        if result.returncode != 0:
            return None
        try:
            policy = json.loads(result.stdout)[0]["HostConfig"]["RestartPolicy"]["Name"]
        except (ValueError, KeyError, IndexError, TypeError):
            return None
        return policy or "no"

    def is_kind_worker_of(self, name: str, cluster: str) -> tuple[dict[str, Any] | None, str | None]:
        """(inspection, None) when ``name`` is a worker of kind cluster ``cluster``; otherwise (inspection or None, why not)."""
        info = self.inspect(name)
        if info is None:
            return None, "CONTAINER_NOT_FOUND"
        labels = info["labels"]
        if labels.get(KIND_CLUSTER_LABEL) != cluster or labels.get(KIND_ROLE_LABEL) != "worker":
            return info, "CONTAINER_MISMATCH"
        return info, None


# -- records ---------------------------------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return          # not every platform can open a directory; the file itself was synced
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_record(directory: Path, record: dict[str, Any]) -> Path:
    """Write the record atomically and durably: a reader sees the old file or the new one, never half of one."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{record['recordId']}.json"
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".recovery-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    _fsync_directory(directory)
    return target


def load_records(directory: Path) -> list[dict[str, Any]]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return [json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))]


def pending_records(directory: Path) -> list[dict[str, Any]]:
    return [r for r in load_records(directory) if r.get("state") != RESTORED]


def restore_container(docker: DockerCli, record: dict[str, Any], directory: Path) -> str | None:
    """Start the container of a pending record and close it. Returns None on success, otherwise why it did not work.

    The container is verified to be a worker of the record's kind cluster before it is started, and the record is updated
    either way, so the file always says what is known.
    """
    name, cluster = record["container"], record["cluster"]
    info, problem = docker.is_kind_worker_of(name, cluster)
    if problem is None and not info["running"]:
        started = docker.run("start", name)
        if started.returncode != 0:
            problem = f"docker start failed: {started.stderr.strip() or started.returncode}"
        else:
            info = docker.inspect(name)
            if info is None or not info["running"]:
                problem = "the container does not report running after docker start"
    if problem is None:
        save_record(directory, {**record, "state": RESTORED, "restoredAt": now_iso(), "lastError": None})
        return None
    save_record(directory, {**record, "state": "RESTORE_FAILED", "lastError": problem})
    return problem


def restore_pending(directory: Path, docker: DockerCli, *, cluster: str) -> dict[str, list[str]]:
    """Restore every pending recovery of ``cluster``. Records of any other cluster are listed as skipped, untouched."""
    outcome: dict[str, list[str]] = {"restored": [], "failed": [], "skipped": []}
    for record in pending_records(directory):
        if record.get("cluster") != cluster:
            outcome["skipped"].append(record["container"])
        elif restore_container(docker, record, Path(directory)) is None:
            outcome["restored"].append(record["container"])
        else:
            outcome["failed"].append(record["container"])
    return outcome


def main(argv: Sequence[str] | None = None, *, runner: Runner = subprocess_runner) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.integrations.kind.recovery", description=__doc__.split("\n\n")[0])
    parser.add_argument("directory")
    parser.add_argument("--restore", action="store_true", help="start the stopped workers (default: only list)")
    parser.add_argument("--cluster", help="the kind cluster whose workers may be started (required with --restore)")
    args = parser.parse_args(argv)
    pending = pending_records(Path(args.directory))
    if not args.restore:
        for record in pending:
            print(f"PENDING {record['container']} (cluster {record['cluster']}, state {record['state']}): "
                  f"run {' '.join(record['restoreCommand'])}")
        print(f"{len(pending)} pending recovery record(s).")
        return 2 if pending else 0
    if not args.cluster:
        parser.error("--restore needs --cluster")
    outcome = restore_pending(Path(args.directory), DockerCli(runner), cluster=args.cluster)
    for key, names in outcome.items():
        for name in names:
            print(f"{key.upper()} {name}")
    return 1 if outcome["failed"] else 0


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())
