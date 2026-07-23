from app.shared.schemas.base import APIModel


class RecommendationCandidate(APIModel):
    id: str
    type: str
    service: str
    feasible: bool
    predicted_blast_radius: float
    risk_reduction: float
    resource_cost: str
    confidence: float
    score: float


class RecommendationReport(APIModel):
    workflow_id: str
    recommendations: list[RecommendationCandidate]
    selected_recommendation_id: str | None = None