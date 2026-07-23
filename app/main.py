from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import (
    deployments,
    failure_impacts,
    health,
    recommendations,
    validations,
    workflows,
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

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)

app.include_router(health.router)
app.include_router(workflows.router)
app.include_router(deployments.router)
app.include_router(failure_impacts.router)
app.include_router(recommendations.router)
app.include_router(validations.router)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "page_title": "K8s Resilience Engine",
        },
    )