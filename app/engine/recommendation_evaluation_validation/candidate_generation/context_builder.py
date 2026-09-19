from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import FailureImpactReport
from app.shared.schemas.workflow import WorkflowGraph

from .models import CandidateContext


class CandidateContextBuilder:
    """Assembles a CandidateContext from upstream component outputs.

    Only wraps Member 2/3 results as-is; it must not re-detect placement
    or failure root causes.
    """

    @staticmethod
    def build(
        deployment: DeploymentGraph,
        failure_impact: FailureImpactReport,
        workflow: WorkflowGraph | None = None,
    ) -> CandidateContext:
        return CandidateContext(
            deployment=deployment,
            failure_impact=failure_impact,
            workflow=workflow,
        )
