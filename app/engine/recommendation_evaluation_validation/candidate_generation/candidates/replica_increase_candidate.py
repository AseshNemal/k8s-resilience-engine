from ..models import CandidateContext, CandidateSpec
from .base import CandidateBuilder


class ReplicaIncreaseCandidateBuilder(CandidateBuilder):
    """Unreachable while ReplicaIncreaseRule.is_applicable is False."""

    def build(self, context: CandidateContext) -> list[CandidateSpec]:
        return []
