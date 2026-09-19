from abc import ABC, abstractmethod

from ..models import CandidateContext, CandidateSpec


class CandidateBuilder(ABC):
    """Constructs the CandidateSpec(s) for a strategy WHAT to recommend."""

    @abstractmethod
    def build(self, context: CandidateContext) -> list[CandidateSpec]:
        ...
