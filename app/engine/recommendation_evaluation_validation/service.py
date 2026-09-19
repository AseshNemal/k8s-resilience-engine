from app.engine.recommendation_evaluation_validation.candidate_generation import (
    CandidateContextBuilder,
    generate_candidates,
)
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    check_topology_spread_feasibility,
)
from app.engine.recommendation_evaluation_validation.metrics import (
    calculate_replica_concentration,
)
from app.engine.recommendation_evaluation_validation.simulation_result import (
    SimulationResult,
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
    """Load and validate the temporary recommendation sample."""

    data = load_sample_json("recommendation.json")
    return RecommendationReport.model_validate(data)


def get_sample_validation() -> ValidationReport:
    """Load and validate the temporary validation sample."""

    data = load_sample_json("validation.json")
    return ValidationReport.model_validate(data)


def evaluate_first_recommendation(
    deployment: DeploymentGraph,
    failure_impact: FailureImpactReport,
) -> SimulationResult:
    """Generate and simulate the first supported recommendation."""

    context = CandidateContextBuilder.build(deployment, failure_impact)
    candidates = generate_candidates(context)

    if not candidates:
        raise ValueError("No recommendation candidates generated.")

    candidate = candidates[0]

    if candidate.recommendation_type != "TOPOLOGY_SPREAD":
        raise ValueError(
            f"Unsupported recommendation: "
            f"{candidate.recommendation_type}"
        )

    feasibility = check_topology_spread_feasibility(
        deployment,
        candidate.service,
    )

    concentration_before = calculate_replica_concentration(
        deployment,
        candidate.service,
    )

    if not feasibility.feasible:
        return SimulationResult(
            recommendation_type=candidate.recommendation_type,
            service=candidate.service,
            feasible=False,
            feasibility_reason=feasibility.reason,
            concentration_before=concentration_before,
            concentration_after=concentration_before,
            concentration_reduction=0.0,
            simulated_deployment=None,
        )

    virtual_deployment = create_virtual_deployment(deployment)

    simulated_deployment = simulate_topology_spread(
        virtual_deployment,
        candidate.service,
    )

    concentration_after = calculate_replica_concentration(
        simulated_deployment,
        candidate.service,
    )

    return SimulationResult(
        recommendation_type=candidate.recommendation_type,
        service=candidate.service,
        feasible=True,
        feasibility_reason=feasibility.reason,
        concentration_before=concentration_before,
        concentration_after=concentration_after,
        concentration_reduction=round(
            concentration_before - concentration_after,
            2,
        ),
        simulated_deployment=simulated_deployment,
    )