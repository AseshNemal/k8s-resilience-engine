from fastapi import APIRouter

from app.engine.recommendation_evaluation_validation.service import (
    get_sample_recommendation,
)
from app.shared.schemas.recommendation import RecommendationReport


router = APIRouter(
    prefix="/api/recommendations",
    tags=["Recommendation Evaluation"],
)


@router.get(
    "/sample",
    response_model=RecommendationReport,
    response_model_by_alias=True,
)
def read_sample_recommendation() -> RecommendationReport:
    return get_sample_recommendation()