"""Isolated research frontend for the synthetic Component 3 portfolio demo.

The main application mounts this sub-application under an explicitly labelled
synthetic path. Its data never substitutes for live evidence in runtime routes.
"""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from experiments.failure_impact_validation.run_disposable_portfolio_demo import (
    COMPONENT4_HANDOFF_FILE,
    CROSS_LAYER_FILE,
    DEPLOYMENT_FILE,
    METADATA_FILE,
    RESULT_FILE,
    WORKFLOWS_FILE,
    build_disposable_portfolio_artifacts,
    build_disposable_portfolio_generation_artifacts,
)


BASE_DIR = Path(__file__).resolve().parent
SYNTHETIC_PORTFOLIO_PATH = "/components/failure-impacts/synthetic-portfolio"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(
    title="Component 3 Synthetic Portfolio Demonstration",
    description=(
        "Research-only UI for explicitly synthetic cross-layer inputs and "
        "virtual single-worker-node failure simulation."
    ),
    version="0.1.0",
)
app.mount(
    "/assets",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="assets",
)


class ScenarioRequest(BaseModel):
    """Identify one deterministic, disposable demonstration scenario."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    scenario_id: str = Field(alias="scenarioId")


class NodeFailureRequest(ScenarioRequest):
    """Select the worker node to fail virtually."""

    failed_node: str = Field(alias="failedNode")


def _generation_response(artifacts: dict[str, object]) -> dict[str, object]:
    return {
        "metadata": artifacts[METADATA_FILE],
        "workflowArtifact": artifacts[WORKFLOWS_FILE],
        "deploymentGraph": artifacts[DEPLOYMENT_FILE],
        "crossLayerArtifact": artifacts[CROSS_LAYER_FILE],
    }


def _generated_at() -> datetime:
    return datetime.now(timezone.utc)


@app.get("/", response_class=HTMLResponse)
def portfolio_demo_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="disposable_portfolio.html",
        context={"page_title": "Synthetic Cross-Layer Portfolio"},
    )


@app.post("/api/synthetic/generate")
def generate_synthetic_portfolio(request: ScenarioRequest) -> dict[str, object]:
    """Generate four assumed workflows and their complete cross-layer graphs."""

    try:
        artifacts = build_disposable_portfolio_generation_artifacts(
            scenario_id=request.scenario_id,
            generated_at=_generated_at(),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _generation_response(artifacts)


@app.post("/api/synthetic/fail-all")
def simulate_all_worker_nodes(request: ScenarioRequest) -> dict[str, object]:
    """Evaluate each worker node independently against the synthetic portfolio."""

    try:
        artifacts = build_disposable_portfolio_artifacts(
            scenario_id=request.scenario_id,
            failed_nodes=[],
            generated_at=_generated_at(),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        **_generation_response(artifacts),
        "resultArtifact": artifacts[RESULT_FILE],
        "component4Handoff": artifacts[COMPONENT4_HANDOFF_FILE],
    }


@app.post("/api/synthetic/fail-one")
def simulate_one_worker_node(request: NodeFailureRequest) -> dict[str, object]:
    """Evaluate one selected worker node against the synthetic portfolio."""

    try:
        artifacts = build_disposable_portfolio_artifacts(
            scenario_id=request.scenario_id,
            failed_nodes=[request.failed_node],
            generated_at=_generated_at(),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        **_generation_response(artifacts),
        "resultArtifact": artifacts[RESULT_FILE],
        "component4Handoff": artifacts[COMPONENT4_HANDOFF_FILE],
    }
