from app.engine.recommendation_evaluation_validation.candidate_generation import (
    CandidateContextBuilder,
    CandidateSpec,
    generate_candidates,
)
from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    SchedulingEvidence,
    check_topology_spread_feasibility,
)
from app.engine.recommendation_evaluation_validation.metrics import (
    calculate_replica_concentration,
)
from app.engine.recommendation_evaluation_validation.mutations import (
    ReplicaIncreaseMutation,
    TopologySpreadMutation,
)
from app.engine.recommendation_evaluation_validation.simulation_result import (
    SimulationResult,
)
from app.engine.recommendation_evaluation_validation.strategy_feasibility import (
    ADDED_MARKER,
    check_pod_anti_affinity_feasibility,
    check_replica_increase_feasibility,
)
from app.engine.simulation import simulate_deployment_change
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
    evidence: SchedulingEvidence | None = None,
) -> SimulationResult:
    """Generate and simulate the first supported recommendation."""

    context = CandidateContextBuilder.build(deployment, failure_impact)
    candidates = generate_candidates(context)

    if not candidates:
        raise ValueError("No recommendation candidates generated.")

    return evaluate_candidate(deployment, candidates[0], evidence)


def _spread_mutation(candidate: CandidateSpec, feasibility):
    return TopologySpreadMutation(candidate.service, feasibility.placement)


def _replica_mutation(candidate: CandidateSpec, feasibility):
    added = tuple(sorted(name for name in feasibility.placement if ADDED_MARKER in name))
    return ReplicaIncreaseMutation(candidate.service, feasibility.placement, added)


# Strategy -> (feasibility check, how the verified placement is applied to the virtual copy).
_STRATEGIES = {
    "TOPOLOGY_SPREAD": (check_topology_spread_feasibility, _spread_mutation),
    "POD_ANTI_AFFINITY": (check_pod_anti_affinity_feasibility, _spread_mutation),
    "REPLICA_INCREASE": (check_replica_increase_feasibility, _replica_mutation),
}


def evaluate_candidate(
    deployment: DeploymentGraph,
    candidate: CandidateSpec,
    evidence: SchedulingEvidence | None = None,
) -> SimulationResult:
    """Check one candidate's feasibility and simulate it on a copy of the deployment.

    The deployment passed in is never changed. Only a candidate whose feasibility is verified is simulated,
    and it is simulated on the placement the feasibility check verified; one that is infeasible or whose
    evidence is insufficient is reported as not feasible and not simulated. ``evidence`` carries the
    scheduling facts the graph cannot (node taints). Raises ValueError for a recommendation type that is not
    supported yet, so a caller can skip it instead of guessing an outcome.
    """

    if candidate.recommendation_type not in _STRATEGIES:
        raise ValueError(
            f"Unsupported recommendation: "
            f"{candidate.recommendation_type}"
        )
    check, mutation_for = _STRATEGIES[candidate.recommendation_type]

    feasibility = check(
        deployment,
        candidate.service,
        evidence,
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
            feasibility_status=feasibility.status,
            feasibility=feasibility,
        )

    mutation = mutation_for(candidate, feasibility)
    sim_result = simulate_deployment_change(deployment, mutation)

    concentration_after = calculate_replica_concentration(
        sim_result.after,
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
        simulated_deployment=sim_result.after,
        feasibility_status=feasibility.status,
        feasibility=feasibility,
        pods_to_recreate=feasibility.pods_to_recreate,
        added_replicas=getattr(mutation, "_added", ()),
    )
