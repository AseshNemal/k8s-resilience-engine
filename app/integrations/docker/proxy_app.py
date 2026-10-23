"""The restricted Docker Engine proxy: the only process that has the Docker socket.

Run as its own container (see ``extension/compose.yaml``), listening on a Unix socket shared with the backend and forwarding to
the Docker socket only what ``policy.decide`` allows. It has no published port. A refused request gets a 403 with the reason and
nothing reaches Docker. Inspect answers are trimmed to the fields a run needs.

    uvicorn app.integrations.docker.proxy_app:app --uds /run/docker-proxy/proxy.sock

Configuration comes from the environment of THIS container, which the backend cannot change:
``RESILIENCE_DOCKER_SOCKET`` (upstream, default /var/run/docker.sock) and ``RESILIENCE_PROXY_ALLOWED_CLUSTERS`` (comma-separated
kind cluster names whose workers may be stopped; default ``resilience-validation``). Adding ``desktop`` there is the deliberate act
that makes the Docker Desktop research cluster reachable at all.
"""

import json
import os
from collections.abc import Callable

import httpx
from fastapi import FastAPI, Request, Response

from app.integrations.docker.policy import DEFAULT_ALLOWED_CLUSTERS, Decision, decide

TIMEOUT_S = 30.0


def allowed_clusters_from_env(env: dict[str, str] | None = None) -> frozenset[str]:
    raw = (env if env is not None else os.environ).get("RESILIENCE_PROXY_ALLOWED_CLUSTERS", "")
    names = frozenset(part.strip().lower() for part in raw.split(",") if part.strip())
    return names or DEFAULT_ALLOWED_CLUSTERS


def trim_inspection(body: dict) -> dict:
    """Only what a run needs about a container: never its environment, mounts or command."""
    state = body.get("State") or {}
    return {"Id": body.get("Id"), "Name": body.get("Name"),
            "Config": {"Labels": (body.get("Config") or {}).get("Labels") or {}},
            "State": {key: state.get(key) for key in ("Running", "Status", "StartedAt", "FinishedAt", "ExitCode", "OOMKilled")},
            "HostConfig": {"RestartPolicy": (body.get("HostConfig") or {}).get("RestartPolicy")}}


def create_app(*, transport: httpx.BaseTransport | None = None, allowed_clusters: frozenset[str] | None = None,
               socket_path: str | None = None) -> FastAPI:
    clusters = allowed_clusters if allowed_clusters is not None else allowed_clusters_from_env()
    upstream = httpx.Client(transport=transport or httpx.HTTPTransport(uds=socket_path or os.environ.get(
        "RESILIENCE_DOCKER_SOCKET", "/var/run/docker.sock")), base_url="http://docker", timeout=TIMEOUT_S)
    app = FastAPI(title="Resilience Docker proxy", docs_url=None, redoc_url=None, openapi_url=None)

    def labels_of(name: str) -> dict[str, str] | None:
        try:
            answer = upstream.get(f"/containers/{name}/json")
        except httpx.HTTPError:
            return None
        if answer.status_code != 200:
            return None
        try:
            return dict((answer.json().get("Config") or {}).get("Labels") or {})
        except ValueError:
            return None

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def forward(path: str, request: Request) -> Response:
        full = "/" + path
        decision: Decision = decide(request.method, full, request.url.query, labels_of, allowed_clusters=clusters)
        if not decision.allowed:
            return Response(json.dumps({"message": decision.reason}), status_code=403, media_type="application/json")
        target = full + (f"?{request.url.query}" if request.url.query else "")
        try:
            answer = upstream.request(request.method, target)
        except httpx.HTTPError as error:
            return Response(json.dumps({"message": f"Docker did not answer: {type(error).__name__}"}), status_code=502,
                            media_type="application/json")
        if decision.action == "json" and answer.status_code == 200:
            return Response(json.dumps(trim_inspection(answer.json())), media_type="application/json")
        return Response(answer.content, status_code=answer.status_code,
                        media_type=answer.headers.get("content-type", "application/json"))

    return app


def _build() -> FastAPI:      # pragma: no cover  (needs the real socket path only when actually served)
    return create_app()


app: FastAPI | Callable = _build()
