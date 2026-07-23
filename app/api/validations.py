from fastapi import APIRouter

from app.engine.recommendation_evaluation_validation.service import (
    get_sample_validation,
)
from app.shared.schemas.validation import ValidationReport


router = APIRouter(
    prefix="/api/validations",
    tags=["Recommendation Validation"],
)


@router.get(
    "/sample",
    response_model=ValidationReport,
    response_model_by_alias=True,
)
def read_sample_validation() -> ValidationReport:
    return get_sample_validation()