from ..models import CandidateContext, CandidateSpec
from ..rules.replica_variants_rule import variant_services
from .base import CandidateBuilder

REPLICA_INCREASE_ANTI_AFFINITY = "REPLICA_INCREASE_ANTI_AFFINITY"
REPLICA_INCREASE_PLUS_2 = "REPLICA_INCREASE_PLUS_2"


class ReplicaVariantsCandidateBuilder(CandidateBuilder):
    """For each concentrated service: one more replica kept apart by required anti-affinity, and two more kept apart by a spread."""

    def build(self, context: CandidateContext) -> list[CandidateSpec]:
        specs: list[CandidateSpec] = []
        for cause in variant_services(context):
            specs.append(CandidateSpec(
                recommendation_type=REPLICA_INCREASE_ANTI_AFFINITY, service=cause.service,
                reason=(f"{cause.service} is concentrated in failure domain {cause.failure_domain}. Adding one replica under a required "
                        f"hostname anti-affinity guarantees that no two replicas share a node, so the loss of one node cannot take "
                        f"them all.")))
            specs.append(CandidateSpec(
                recommendation_type=REPLICA_INCREASE_PLUS_2, service=cause.service,
                reason=(f"{cause.service} is concentrated in failure domain {cause.failure_domain}. Adding two replicas, kept spread over "
                        f"the nodes, leaves more of the service running when one node fails, at twice the added resource cost.")))
        return specs
