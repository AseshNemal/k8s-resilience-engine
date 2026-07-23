"""Overview dashboard page route.

Renders a template only — no research logic here.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web.templates import templates


router = APIRouter(tags=["Web: Overview"])


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "page_title": "Overview",
            "active_page": "overview",
        },
    )
