from app.shared.sample_loader import load_sample_json
from app.shared.schemas.recommendation import RecommendationReport
from app.shared.schemas.validation import ValidationReport


def get_sample_recommendation() -> RecommendationReport:
    data = load_sample_json("recommendation.json")
    return RecommendationReport.model_validate(data)


def get_sample_validation() -> ValidationReport:
    data = load_sample_json("validation.json")
    return ValidationReport.model_validate(data)