"""Deployment Topology Modeling page route (Member 2).

Renders a template only — no research logic here.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web.templates import templates


router = APIRouter(tags=["Web: Deployment Topology Modeling"])


@router.get("/components/deployments", response_class=HTMLResponse)
def deployments_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="components/deployments.html",
        context={
            "page_title": "Deployment Topology Modeling",
            "active_page": "deployments",
        },
    )
