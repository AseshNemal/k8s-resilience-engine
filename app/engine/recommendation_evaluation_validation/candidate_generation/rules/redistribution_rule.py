from ..models import CandidateContext
from .base import CandidateRule


class RedistributionRule(CandidateRule):
    """No root-cause type in the current schema triggers this strategy yet."""

    def is_applicable(self, context: CandidateContext) -> bool:
        return False
