from ..models import CandidateContext
from .base import CandidateRule


def single_replica_services(context: CandidateContext) -> set[str]:
    """Services that declare and run exactly one replica: the case no change of placement can fix."""
    return {
        service.name
        for service in context.deployment.services
        if service.declared_replicas == 1 and service.ready_replicas == 1
    }


class ReplicaIncreaseRule(CandidateRule):
    """Applies only to a concentrated service that has a single replica ("only when necessary").

    More replicas without placement control would not fix concentration, so the candidate always comes with the
    constraint that keeps the replicas apart (see ``strategy_feasibility``). A service with two or more ready
    replicas is a placement problem, not a replica-count problem, and is left to the placement strategies.
    """

    def is_applicable(self, context: CandidateContext) -> bool:
        single = single_replica_services(context)
        return any(
            cause.type == "REPLICA_CONCENTRATION" and cause.service in single
            for cause in context.failure_impact.root_causes
        )
