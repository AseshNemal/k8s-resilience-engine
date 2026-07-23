"""Recommendation Evaluation and Validation page route (Member 4).

Renders a template only — no research logic here.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web.templates import templates


router = APIRouter(tags=["Web: Recommendation Evaluation and Validation"])


@router.get("/components/recommendations", response_class=HTMLResponse)
def recommendations_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="components/recommendations.html",
        context={
            "page_title": "Recommendation Evaluation and Validation",
            "active_page": "recommendations",
        },
    )
