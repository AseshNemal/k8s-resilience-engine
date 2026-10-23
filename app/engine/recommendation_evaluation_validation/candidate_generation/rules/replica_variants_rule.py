from ...strategy_feasibility import has_hostname_spread, has_required_anti_affinity
from ..models import CandidateContext
from .base import CandidateRule


def variant_services(context: CandidateContext) -> list:
    """Root causes whose service can be offered a replica-increase variant, decided from the declared facts alone.

    The service must be in the topology with at least one declared replica and must not already carry a hostname spread or required
    anti-affinity (a patch would rewrite it, so the variant is not a change that could be verified). A service the topology does not
    know, or whose placement constraints are unknown, is not offered the variant: the same convention as the other strategies.
    """
    services = {service.name: service for service in context.deployment.services}
    offered, seen = [], set()
    for cause in context.failure_impact.root_causes:
        service = services.get(cause.service)
        if cause.type != "REPLICA_CONCENTRATION" or service is None or cause.service in seen or service.declared_replicas < 1:
            continue
        if has_hostname_spread(service) or has_required_anti_affinity(service):
            continue
        seen.add(cause.service)
        offered.append(cause)
    return offered


class ReplicaVariantsRule(CandidateRule):
    """Applies when a concentrated service can be given more replicas in the two further forms (see the builder).

    ``ReplicaIncreaseRule`` offers one replica plus a spread for a single-replica service only. These variants are offered for any
    concentrated service, and each is judged by the same constraint search, so one that cannot be placed is reported infeasible with
    its reason, never hidden.
    """

    def is_applicable(self, context: CandidateContext) -> bool:
        return bool(variant_services(context))
