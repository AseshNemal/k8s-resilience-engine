"""A ``Runner`` that stops and starts kind worker containers through the restricted Docker proxy, not through a shell or a socket.

The kind injector and the recovery procedure speak to Docker through a ``Runner``: a function from ``["docker", verb, name]`` to a
``CommandResult``. This runner satisfies that contract by calling the Docker Engine API on the PROXY's Unix socket (the backend has
no Docker socket of its own, and no ``docker`` binary). It accepts only ``inspect``, ``kill`` and ``start`` on a plain container
name, and sends ``kill`` with SIGKILL, so the code that holds the research logic is also limited to those three acts before the
proxy applies its own, independent policy.

``evidence_class`` is ``REAL_CLUSTER`` only when it talks to a real proxy socket; with an injected transport (every test) it is a
test double, so an experiment recorded through it can never be presented as a measurement.

``DockerEngineFacts`` reads what the memory assessment needs (the VM's memory and each running container's usage), read-only.
"""

import json
import os
import re
from collections.abc import Sequence
from typing import Any

import httpx

from app.integrations.kind.recovery import CommandResult

DEFAULT_PROXY_SOCKET = "/run/docker-proxy/proxy.sock"
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
TIMEOUT_S = 60.0


class DockerEngineRunner:
    def __init__(self, *, socket_path: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        path = socket_path or os.environ.get("RESILIENCE_DOCKER_PROXY_SOCKET", DEFAULT_PROXY_SOCKET)
        self.evidence_class = "TEST_DOUBLE" if transport is not None else "REAL_CLUSTER"
        self._client = httpx.Client(transport=transport or httpx.HTTPTransport(uds=path), base_url="http://docker-proxy",
                                    timeout=TIMEOUT_S)

    def __call__(self, args: Sequence[str]) -> CommandResult:
        if len(args) != 3 or args[0] != "docker" or args[1] not in ("inspect", "kill", "start") or not SAFE_NAME.match(args[2] or ""):
            return CommandResult(2, "", f"refused: only 'docker inspect|kill|start <container name>' is supported, got {list(args)!r}")
        verb, name = args[1], args[2]
        try:
            if verb == "inspect":
                answer = self._client.get(f"/containers/{name}/json")
                if answer.status_code == 200:
                    return CommandResult(0, json.dumps([answer.json()]), "")
            elif verb == "kill":
                answer = self._client.post(f"/containers/{name}/kill", params={"signal": "SIGKILL"})
            else:
                answer = self._client.post(f"/containers/{name}/start")
        except httpx.HTTPError as error:
            return CommandResult(1, "", f"the Docker proxy could not be reached: {type(error).__name__}")
        if answer.status_code in (204, 304):      # 304: already started, which is what a restore wants
            return CommandResult(0, "", "")
        return CommandResult(1, "", f"Error response from daemon: {answer.status_code} {_message(answer)}")


def _message(answer: httpx.Response) -> str:
    try:
        return str(answer.json().get("message", ""))
    except ValueError:
        return answer.text[:200]


class DockerEngineFacts:
    """Read-only facts about the Docker VM, for the memory assessment. Never changes anything."""

    def __init__(self, *, socket_path: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        path = socket_path or os.environ.get("RESILIENCE_DOCKER_PROXY_SOCKET", DEFAULT_PROXY_SOCKET)
        self._client = httpx.Client(transport=transport or httpx.HTTPTransport(uds=path), base_url="http://docker-proxy",
                                    timeout=TIMEOUT_S)

    def info(self) -> dict[str, Any]:
        answer = self._client.get("/info")
        answer.raise_for_status()
        body = answer.json()
        return {"memTotalBytes": body.get("MemTotal"), "ncpu": body.get("NCPU"), "serverVersion": body.get("ServerVersion"),
                "containersRunning": body.get("ContainersRunning")}

    def container_memory(self) -> dict[str, int]:
        """Memory in use per running container, in bytes (one-shot stats; a container that cannot be read is left out)."""
        listing = self._client.get("/containers/json")
        listing.raise_for_status()
        usage: dict[str, int] = {}
        for container in listing.json():
            names = [n.lstrip("/") for n in container.get("Names") or []]
            if not names or not SAFE_NAME.match(names[0]):
                continue
            stats = self._client.get(f"/containers/{names[0]}/stats", params={"stream": "false", "one-shot": "true"})
            if stats.status_code != 200:
                continue
            memory = (stats.json().get("memory_stats") or {})
            used = memory.get("usage")
            if isinstance(used, int):
                cache = (memory.get("stats") or {}).get("inactive_file") or (memory.get("stats") or {}).get("cache") or 0
                usage[names[0]] = max(used - int(cache), 0)
        return usage
