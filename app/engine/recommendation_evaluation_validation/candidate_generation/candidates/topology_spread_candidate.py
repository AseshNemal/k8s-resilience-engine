from ..models import CandidateContext, CandidateSpec
from .base import CandidateBuilder


class TopologySpreadCandidateBuilder(CandidateBuilder):
    def build(self, context: CandidateContext) -> list[CandidateSpec]:
        return [
            CandidateSpec(
                recommendation_type="TOPOLOGY_SPREAD",
                service=cause.service,
                reason=(
                    f"Replicas of {cause.service} are concentrated "
                    f"in {cause.failure_domain}."
                ),
            )
            for cause in context.failure_impact.root_causes
            if cause.type == "REPLICA_CONCENTRATION"
        ]
