from abc import ABC, abstractmethod

from ..models import CandidateContext


class CandidateRule(ABC):
    """Determines WHEN a recommendation strategy applies to a context."""

    @abstractmethod
    def is_applicable(self, context: CandidateContext) -> bool:
        ...
