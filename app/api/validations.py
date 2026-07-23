from fastapi import APIRouter

router = APIRouter(
    prefix="/api/validations",
    tags=["Recommendation Validation"],
)


@router.get("/sample")
def get_sample_validation() -> dict:
    return {
        "recommendationId": "rec-001",
        "predictedBlastRadius": 15,
        "observedBlastRadius": 20,
        "predictionError": 5,
        "validated": True,
    }