from ..models import CandidateContext, CandidateSpec
from ..rules.pod_anti_affinity_rule import hardenable_services
from .base import CandidateBuilder


class PodAntiAffinityCandidateBuilder(CandidateBuilder):
    """Build a candidate that hardens existing soft anti-affinity into a required rule."""

    def build(self, context: CandidateContext) -> list[CandidateSpec]:
        hardenable = hardenable_services(context)
        return [
            CandidateSpec(
                recommendation_type="POD_ANTI_AFFINITY",
                service=cause.service,
                reason=(
                    f"The replicas of {cause.service} are concentrated in failure domain {cause.failure_domain} although "
                    f"its Deployment already asks, softly, for its replicas to avoid sharing a node. A preferred rule does "
                    f"not guarantee that; a required pod anti-affinity (one replica per node) does."
                ),
            )
            for cause in context.failure_impact.root_causes
            if cause.type == "REPLICA_CONCENTRATION" and cause.service in hardenable
        ]
