from .models import CandidateContext, CandidateSpec
from .registry import STRATEGIES


def generate_candidates(context: CandidateContext) -> list[CandidateSpec]:
    candidates: list[CandidateSpec] = []

    for strategy in STRATEGIES:
        if strategy.rule.is_applicable(context):
            candidates.extend(strategy.builder.build(context))

    return candidates
