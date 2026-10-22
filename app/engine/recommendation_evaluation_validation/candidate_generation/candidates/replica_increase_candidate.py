from ..models import CandidateContext, CandidateSpec
from ..rules.replica_increase_rule import single_replica_services
from .base import CandidateBuilder


class ReplicaIncreaseCandidateBuilder(CandidateBuilder):
    """Build a replica increase for each concentrated single-replica service."""

    def build(self, context: CandidateContext) -> list[CandidateSpec]:
        single = single_replica_services(context)
        return [
            CandidateSpec(
                recommendation_type="REPLICA_INCREASE",
                service=cause.service,
                reason=(
                    f"{cause.service} runs a single replica in failure domain {cause.failure_domain}, so no change of "
                    f"placement can keep it available when that node fails. Adding one replica, kept on a different "
                    f"node by a hostname spread constraint, is a candidate."
                ),
            )
            for cause in context.failure_impact.root_causes
            if cause.type == "REPLICA_CONCENTRATION" and cause.service in single
        ]
