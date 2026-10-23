"""The Development Lab web app: a standalone, localhost-only server, never mounted by ``app.main``.

Isolation, by construction:
* this module lives under ``experiments/`` and nothing in ``app/`` or ``extension/`` imports it;
* it serves only GET routes (there is nothing to approve, apply or write through it);
* it binds to 127.0.0.1 only;
* every response is labelled as offline controlled evidence.
"""

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.engine.recommendation_evaluation_validation.ranking import RankingWeights
from app.shared.evidence_errors import EvidenceError
from experiments.member4_dev_lab import lab_service
from experiments.member4_dev_lab.lab_service import LAB_LABEL

HERE = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=HERE / "templates")


def _weights(target_share: float | None, disruption_discount: float | None,
             resource_discount: float | None) -> RankingWeights | None:
    """Custom ranking weights for a sensitivity check, or None for the engine's experimental defaults."""
    if target_share is None and disruption_discount is None and resource_discount is None:
        return None
    defaults = RankingWeights()
    return RankingWeights(
        target_share=target_share if target_share is not None else defaults.target_share,
        disruption_discount=disruption_discount if disruption_discount is not None else defaults.disruption_discount,
        resource_discount=resource_discount if resource_discount is not None else defaults.resource_discount,
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="Member 4 Development Lab (offline)",
        description=LAB_LABEL,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="dev-lab-static")

    @app.exception_handler(EvidenceError)
    async def evidence_error(request: Request, error: EvidenceError) -> JSONResponse:
        return JSONResponse(status_code=error.http_status, content={"detail": error.detail()})

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return templates.TemplateResponse(request, "lab.html", {"label": LAB_LABEL})

    @app.get("/api/dev-lab/scenarios")
    def scenarios() -> dict:
        return {"label": LAB_LABEL, "scenarios": lab_service.list_scenarios()}

    @app.get("/api/dev-lab/scenarios/{name}")
    def scenario(
        name: str,
        target_share: float | None = Query(default=None, ge=0, le=1),
        disruption_discount: float | None = Query(default=None, ge=0, le=1),
        resource_discount: float | None = Query(default=None, ge=0, le=1),
    ) -> dict:
        try:
            weights = _weights(target_share, disruption_discount, resource_discount)
        except ValueError as error:
            return JSONResponse(status_code=422, content={"detail": str(error)})
        try:
            return lab_service.scenario_detail(name, weights)
        except lab_service.ScenarioNotFound:
            return JSONResponse(status_code=404, content={"detail": f"Unknown scenario: {name}"})

    @app.get("/api/dev-lab/experiment")
    def experiment() -> dict:
        return lab_service.experiment_overview()

    return app


app = create_app()
