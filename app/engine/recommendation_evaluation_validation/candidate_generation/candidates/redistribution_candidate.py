from ..models import CandidateContext, CandidateSpec
from .base import CandidateBuilder


class RedistributionCandidateBuilder(CandidateBuilder):
    """Unreachable while RedistributionRule.is_applicable is False."""

    def build(self, context: CandidateContext) -> list[CandidateSpec]:
        return []
