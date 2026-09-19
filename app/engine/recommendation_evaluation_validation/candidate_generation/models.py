from dataclasses import dataclass

from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import FailureImpactReport
from app.shared.schemas.workflow import WorkflowGraph


@dataclass
class CandidateContext:
    deployment: DeploymentGraph
    failure_impact: FailureImpactReport
    workflow: WorkflowGraph | None = None


@dataclass
class CandidateSpec:
    recommendation_type: str
    service: str
    reason: str
