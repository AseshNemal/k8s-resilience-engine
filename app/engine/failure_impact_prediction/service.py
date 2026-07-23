from app.shared.sample_loader import load_sample_json
from app.shared.schemas.failure_impact import FailureImpactReport


def get_sample_failure_impact() -> FailureImpactReport:
    data = load_sample_json("failure_impact.json")
    return FailureImpactReport.model_validate(data)