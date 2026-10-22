"""Read-only tracing routes for the Docker Desktop extension.

  GET /_extension/tracing/connection    where the query service and the OTLP receivers are
  GET /_extension/tracing/status        whether the query service answers and whether telemetry is arriving
  GET /_extension/tracing/applications  whether the named Kubernetes services are producing traces

They only read what Jaeger reports (see app/integrations/tracing). They start nothing, change nothing in a
cluster, and take no address from the caller: the trace service is the backend's own setting. Starting and
stopping the trace container is the host helper's job and needs the person's approval; it is not done here.
This router is attached in extension/asgi.py, so app/main.py and every shared file stay as they are.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.integrations.jaeger.client import JaegerClient
from app.integrations.tracing import match_applications, read_status, resolve_connection
from app.integrations.tracing.applications import MAX_SERVICES, valid_name

router = APIRouter(prefix="/_extension/tracing", tags=["Extension tracing"])

# Short, so a page that polls is never held up for long by a trace service that has gone quiet.
QUERY_TIMEOUT_SECONDS = 3.0


def make_source() -> JaegerClient:
    """The backend's trace service. Tests replace this; callers cannot."""
    return JaegerClient(base_url=resolve_connection().query_url, timeout_seconds=QUERY_TIMEOUT_SECONDS)


def _coded(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.get("/connection")
def get_connection() -> dict:
    return resolve_connection().to_mapping()


@router.get("/status")
def get_status() -> dict:
    # An unreachable trace service is a normal answer (telemetry.state UNREACHABLE), so a page can wait and retry.
    return read_status(make_source())


@router.get("/applications")
def get_applications(namespace: str = Query(...), service: list[str] = Query(default=[])) -> dict:
    if not valid_name(namespace) or len(service) > MAX_SERVICES or not all(valid_name(name) for name in service):
        raise _coded(422, "INVALID_NAME", "The namespace and service names must be Kubernetes names.")
    try:
        return match_applications(make_source(), namespace, service)
    except ConnectionError:
        raise _coded(503, "TRACE_SERVICE_UNAVAILABLE", "The trace service did not answer.") from None
