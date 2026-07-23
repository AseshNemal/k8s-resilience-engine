from fastapi import APIRouter

from app.engine.failure_impact_prediction.service import (
    get_sample_failure_impact,
)
from app.shared.schemas.failure_impact import FailureImpactReport


router = APIRouter(
    prefix="/api/failure-impacts",
    tags=["Failure Impact Prediction"],
)


@router.get(
    "/sample",
    response_model=FailureImpactReport,
    response_model_by_alias=True,
)
def read_sample_failure_impact() -> FailureImpactReport:
    return get_sample_failure_impact()