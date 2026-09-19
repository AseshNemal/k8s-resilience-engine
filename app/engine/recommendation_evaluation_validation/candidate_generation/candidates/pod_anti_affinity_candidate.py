from ..models import CandidateContext, CandidateSpec
from .base import CandidateBuilder


class PodAntiAffinityCandidateBuilder(CandidateBuilder):
    """Unreachable while PodAntiAffinityRule.is_applicable is False."""

    def build(self, context: CandidateContext) -> list[CandidateSpec]:
        return []
