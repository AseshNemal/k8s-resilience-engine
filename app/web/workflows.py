"""Workflow Dependency Modeling page route (Member 1).

Renders a template only — no research logic here.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web.templates import templates


router = APIRouter(tags=["Web: Workflow Dependency Modeling"])


@router.get("/components/workflows", response_class=HTMLResponse)
def workflows_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="components/workflows.html",
        context={
            "page_title": "Workflow Dependency Modeling",
            "active_page": "workflows",
        },
    )
