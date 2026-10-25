from pathlib import Path

from starlette.routing import Mount

from app.main import app
from experiments.failure_impact_validation.portfolio_demo_app import (
    SYNTHETIC_PORTFOLIO_PATH,
    app as portfolio_demo_app,
)


FAILURE_IMPACT_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "templates"
    / "components"
    / "failure_impacts.html"
)


def test_main_application_mounts_the_explicit_synthetic_portfolio() -> None:
    mounts = [route for route in app.routes if isinstance(route, Mount)]
    portfolio_mount = next(
        mount for mount in mounts if mount.path == SYNTHETIC_PORTFOLIO_PATH
    )

    assert portfolio_mount.app is portfolio_demo_app


def test_normal_runtime_application_does_not_gain_synthetic_routes() -> None:
    runtime_paths = app.openapi()["paths"]

    assert "/api/synthetic/generate" not in runtime_paths
    assert "/api/synthetic/fail-one" not in runtime_paths
    assert "/api/synthetic/fail-all" not in runtime_paths


def test_failure_impact_page_links_to_the_explicit_research_mount() -> None:
    template = FAILURE_IMPACT_TEMPLATE.read_text(encoding="utf-8")

    assert 'data-role="synthetic-portfolio-link"' in template
    assert (
        'href="/components/failure-impacts/synthetic-portfolio/"'
        in template
    )
    assert "Open Synthetic Portfolio" in template
    assert "not live cluster evidence" in template
