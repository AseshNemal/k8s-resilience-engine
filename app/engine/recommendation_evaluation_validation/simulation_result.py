from dataclasses import dataclass

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