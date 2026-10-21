from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    deployments,
    failure_impacts,
    health,
    recommendations,
    workflows,
)
from app.shared.evidence_errors import EvidenceError
from app.web import (
    dashboard,
    deployments as web_deployments,
    failure_impacts as web_failure_impacts,
    recommendations as web_recommendations,
    workflows as web_workflows,
)


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="K8s Resilience Engine",
    description=(
        "Dependency-aware failure-domain resilience analysis "
        "for Kubernetes microservices."
    ),
    version="0.1.0",
)



@app.exception_handler(EvidenceError)
async def evidence_unavailable(_request: Request, error: EvidenceError) -> JSONResponse:
    """Missing live evidence is an explicit, machine-readable error, never a substituted answer."""
    return JSONResponse(status_code=error.http_status, content={"detail": error.detail()})


app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

# JSON API routers
app.include_router(health.router)
app.include_router(workflows.router)
app.include_router(deployments.router)
app.include_router(failure_impacts.router)
app.include_router(recommendations.router)

# HTML page routers (app/web/) — render templates only, no research logic.
app.include_router(dashboard.router)
app.include_router(web_workflows.router)
app.include_router(web_deployments.router)
app.include_router(web_failure_impacts.router)
app.include_router(web_recommendations.router)