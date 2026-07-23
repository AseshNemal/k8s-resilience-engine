from app.shared.schemas.base import APIModel


class ValidationReport(APIModel):
    recommendation_id: str
    predicted_blast_radius: float
    observed_blast_radius: float
    prediction_error: float
    validated: bool