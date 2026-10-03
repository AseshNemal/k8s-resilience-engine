"""
app.engine.simulation — Generic virtual deployment simulation infrastructure.

This package provides ONLY:
  - DeploymentGraph deep-copy / isolation
  - The DeploymentMutation protocol (the contract for a virtual change)
  - DeploymentSimulationResult (before/after snapshots)
  - simulate_deployment_change() (the execution entry-point)

Ownership boundaries
--------------------
Failure semantics (which pods die, service/workflow survival, blast radius,
FailureImpactReport) are owned by Component 3
(app/engine/failure_impact_prediction/).

Recommendation-change semantics (topology spread, pod anti-affinity,
redistribution, replica scaling) are owned by Component 4
(app/engine/recommendation_evaluation_validation/).

Do NOT add any Component 3 or Component 4 concepts here.
"""

from app.engine.simulation.engine import simulate_deployment_change
from app.engine.simulation.models import DeploymentSimulationResult
from app.engine.simulation.protocols import DeploymentMutation

__all__ = [
    "DeploymentMutation",
    "DeploymentSimulationResult",
    "simulate_deployment_change",
]
