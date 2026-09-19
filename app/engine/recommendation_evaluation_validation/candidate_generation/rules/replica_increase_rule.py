from ..models import CandidateContext
from .base import CandidateRule


class ReplicaIncreaseRule(CandidateRule):
    """Never matches REPLICA_CONCENTRATION: more replicas in the same failure
    domain would not fix concentration. No other root-cause type triggers
    this strategy yet.
    """

    def is_applicable(self, context: CandidateContext) -> bool:
        return False
