from ...strategy_feasibility import has_required_anti_affinity, has_soft_self_anti_affinity
from ..models import CandidateContext
from .base import CandidateRule


def hardenable_services(context: CandidateContext) -> set[str]:
    """Services that ask softly for their replicas to avoid sharing a node but have no required rule."""
    return {
        service.name
        for service in context.deployment.services
        if has_soft_self_anti_affinity(service) and not has_required_anti_affinity(service)
    }


class PodAntiAffinityRule(CandidateRule):
    """Applies to a concentrated service whose Deployment already declares SOFT anti-affinity.

    Required hostname anti-affinity admits a subset of the placements a hostname spread does, so it is never a better
    placement and is not offered for every concentrated service. It is offered where the owner has already asked, softly,
    for the replicas to be kept apart: preferred rules do not guarantee that, and making the term required is the
    smallest change that does. Without that signal in the declared intent nothing is offered.
    """

    def is_applicable(self, context: CandidateContext) -> bool:
        hardenable = hardenable_services(context)
        return any(
            cause.type == "REPLICA_CONCENTRATION" and cause.service in hardenable
            for cause in context.failure_impact.root_causes
        )
