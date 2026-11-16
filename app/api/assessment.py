"""One read-only assessment for the connected application. Nothing is applied."""

from fastapi import APIRouter, Query

from app.engine.assessment.product import build_product_assessment


router = APIRouter(prefix="/api/assessment", tags=["Assessment"])


@router.get("")
def read_assessment(
    namespace: str | None = Query(None, description="Application namespace. Omit when the cluster has one application."),
) -> dict:
    """Workflow evidence, every single-worker failure, and the leading feasible change, from one topology snapshot."""
    return build_product_assessment(namespace)
