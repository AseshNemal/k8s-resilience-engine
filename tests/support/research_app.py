"""An application that adds the research-only routes to the production application.

Use ``from tests.support.research_app import app`` in tests that exercise synthetic fixtures,
benchmarks or chaos scenarios over HTTP. The production ``app.main.app`` never serves these routes.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.main import app as production_app
from app.shared.evidence_errors import EvidenceError
from tests.support import research_routes

app = FastAPI(title="Research app (tests only)")


@app.exception_handler(EvidenceError)
async def evidence_unavailable(_request, error: EvidenceError) -> JSONResponse:
    return JSONResponse(status_code=error.http_status, content={"detail": error.detail()})


for router in (
    research_routes.deployments_router,
    research_routes.workflows_router,
    research_routes.failure_impacts_router,
    research_routes.recommendations_router,
    research_routes.validations_router,
):
    app.include_router(router)
app.include_router(production_app.router)
