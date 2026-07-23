"""Failure Impact Prediction page route (Member 3).

Renders a template only — no research logic here.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web.templates import templates


router = APIRouter(tags=["Web: Failure Impact Prediction"])


@router.get("/components/failure-impacts", response_class=HTMLResponse)
def failure_impacts_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="components/failure_impacts.html",
        context={
            "page_title": "Failure Impact Prediction",
            "active_page": "failure_impacts",
        },
    )
