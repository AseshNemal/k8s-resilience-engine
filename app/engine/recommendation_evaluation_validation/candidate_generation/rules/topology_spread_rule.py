from ..models import CandidateContext
from .base import CandidateRule


class TopologySpreadRule(CandidateRule):
    """Applies when any root cause is replica concentration in one domain."""

    def is_applicable(self, context: CandidateContext) -> bool:
        return any(
            cause.type == "REPLICA_CONCENTRATION"
            for cause in context.failure_impact.root_causes
        )
