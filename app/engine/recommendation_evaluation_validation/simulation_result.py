from dataclasses import dataclass

from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    FeasibilityResult,
    FeasibilityStatus,
)
from app.shared.schemas.deployment import DeploymentGraph


@dataclass
class SimulationResult:
    recommendation_type: str
    service: str
    feasible: bool
    feasibility_reason: str
    concentration_before: float
    concentration_after: float
    concentration_reduction: float
    simulated_deployment: DeploymentGraph | None
    # `feasible` stays a plain bool for existing readers: it is True only when the status is FEASIBLE.
    feasibility_status: FeasibilityStatus | None = None
    feasibility: FeasibilityResult | None = None
    # The simulated deployment is the TARGET state. A spread constraint does not move running pods, so it is
    # reached only by recreating these pods.
    pods_to_recreate: tuple[str, ...] = ()

    @property
    def requires_pod_recreation(self) -> bool:
        return bool(self.pods_to_recreate)
