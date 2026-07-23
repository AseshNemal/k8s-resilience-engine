from fastapi import APIRouter

router = APIRouter(
    prefix="/api/recommendations",
    tags=["Recommendation Evaluation and Validation"],
)


@router.get("/sample")
def get_sample_recommendation() -> dict:
    return {
        "workflowId": "checkout",
        "recommendations": [
            {
                "id": "rec-001",
                "type": "TOPOLOGY_SPREAD",
                "service": "paymentservice",
                "feasible": True,
                "predictedBlastRadius": 15,
                "riskReduction": 45,
                "resourceCost": "LOW",
                "confidence": 0.88,
                "score": 0.86,
            }
        ],
        "selectedRecommendationId": "rec-001",
    }