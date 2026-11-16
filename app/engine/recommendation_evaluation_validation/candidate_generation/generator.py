from dataclasses import replace

from .models import CandidateContext, CandidateSpec
from .registry import STRATEGIES


def workflow_relevant(context: CandidateContext) -> CandidateContext:
    """The context with only the root causes a change could matter for: those of the workflow's MANDATORY services.

    Component 3 lists a root cause for every workflow service the failure takes down, optional ones included. A workflow
    survives when its mandatory services do, so a change to an optional service cannot alter the predicted outcome and is
    not a recommendation for this workflow. Without a workflow (a caller that has only the failure report) nothing is
    filtered. The caller's context and Component 3's report are not changed.
    """
    if context.workflow is None:
        return context
    mandatory = set(context.workflow.mandatory_services)
    causes = [cause for cause in context.failure_impact.root_causes if cause.service in mandatory]
    return replace(context, failure_impact=context.failure_impact.model_copy(update={"root_causes": causes}))


def generate_candidates(context: CandidateContext) -> list[CandidateSpec]:
    context = workflow_relevant(context)
    candidates: list[CandidateSpec] = []

    for strategy in STRATEGIES:
        if strategy.rule.is_applicable(context):
            candidates.extend(strategy.builder.build(context))

    return candidates
