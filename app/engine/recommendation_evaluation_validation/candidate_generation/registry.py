from dataclasses import dataclass

from .candidates.base import CandidateBuilder
from .candidates.pod_anti_affinity_candidate import PodAntiAffinityCandidateBuilder
from .candidates.replica_increase_candidate import ReplicaIncreaseCandidateBuilder
from .candidates.replica_variants_candidate import ReplicaVariantsCandidateBuilder
from .candidates.topology_spread_candidate import TopologySpreadCandidateBuilder
from .rules.base import CandidateRule
from .rules.pod_anti_affinity_rule import PodAntiAffinityRule
from .rules.replica_increase_rule import ReplicaIncreaseRule
from .rules.replica_variants_rule import ReplicaVariantsRule
from .rules.topology_spread_rule import TopologySpreadRule


@dataclass(frozen=True)
class StrategyDefinition:
    rule: CandidateRule
    builder: CandidateBuilder


# There is no separate "redistribute existing replicas" strategy. Kubernetes has no operation that moves a running pod, and
# a Deployment's pods deleted by hand are rescheduled wherever the scheduler likes, so a redistribution that is not backed by a
# constraint can be neither written as a patch nor verified. TOPOLOGY_SPREAD is how existing replicas are redistributed here:
# it changes no replica count and its feasibility check reports which replicas end up on a different node.
STRATEGIES: list[StrategyDefinition] = [
    StrategyDefinition(
        rule=TopologySpreadRule(),
        builder=TopologySpreadCandidateBuilder(),
    ),
    StrategyDefinition(
        rule=PodAntiAffinityRule(),
        builder=PodAntiAffinityCandidateBuilder(),
    ),
    StrategyDefinition(
        rule=ReplicaIncreaseRule(),
        builder=ReplicaIncreaseCandidateBuilder(),
    ),
    StrategyDefinition(
        rule=ReplicaVariantsRule(),
        builder=ReplicaVariantsCandidateBuilder(),
    ),
]
