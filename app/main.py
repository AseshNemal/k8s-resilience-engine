from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import (
    deployments,
    failure_impacts,
    health,
    recommendations,
    validations,
    workflows,
)
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
app.include_router(validations.router)

# HTML page routers (app/web/) — render templates only, no research logic.
app.include_router(dashboard.router)
app.include_router(web_workflows.router)
app.include_router(web_deployments.router)
app.include_router(web_failure_impacts.router)
app.include_router(web_recommendations.router)