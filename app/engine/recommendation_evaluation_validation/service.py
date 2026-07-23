from app.engine.recommendation_evaluation_validation.candidate_generator import (
    generate_candidates,
)
from app.engine.recommendation_evaluation_validation.simulator import (
    simulate_topology_spread,
)
from app.engine.recommendation_evaluation_validation.virtual_model import (
    create_virtual_deployment,
)
from app.shared.sample_loader import load_sample_json
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import FailureImpactReport
from app.shared.schemas.recommendation import RecommendationReport
from app.shared.schemas.validation import ValidationReport


def get_sample_recommendation() -> RecommendationReport:
    data = load_sample_json("recommendation.json")
    return RecommendationReport.model_validate(data)


def get_sample_validation() -> ValidationReport:
    data = load_sample_json("validation.json")
    return ValidationReport.model_validate(data)


def simulate_first_recommendation(
    deployment: DeploymentGraph,
    failure_impact: FailureImpactReport,
) -> DeploymentGraph:
    candidates = generate_candidates(failure_impact)

    if not candidates:
        raise ValueError("No recommendation candidates generated.")

    candidate = candidates[0]
    virtual_deployment = create_virtual_deployment(deployment)

    if candidate.recommendation_type == "TOPOLOGY_SPREAD":
        return simulate_topology_spread(
            virtual_deployment,
            candidate.service,
        )

    raise ValueError(
        f"Unsupported recommendation: "
        f"{candidate.recommendation_type}"
    )