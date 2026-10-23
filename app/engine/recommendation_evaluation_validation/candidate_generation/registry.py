from dataclasses import dataclass

from .candidates.base import CandidateBuilder
from .candidates.pod_anti_affinity_candidate import PodAntiAffinityCandidateBuilder
from .candidates.redistribution_candidate import RedistributionCandidateBuilder
from .candidates.replica_increase_candidate import ReplicaIncreaseCandidateBuilder
from .candidates.replica_variants_candidate import ReplicaVariantsCandidateBuilder
from .candidates.topology_spread_candidate import TopologySpreadCandidateBuilder
from .rules.base import CandidateRule
from .rules.pod_anti_affinity_rule import PodAntiAffinityRule
from .rules.redistribution_rule import RedistributionRule
from .rules.replica_increase_rule import ReplicaIncreaseRule
from .rules.replica_variants_rule import ReplicaVariantsRule
from .rules.topology_spread_rule import TopologySpreadRule


@dataclass(frozen=True)
class StrategyDefinition:
    rule: CandidateRule
    builder: CandidateBuilder


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
        rule=RedistributionRule(),
        builder=RedistributionCandidateBuilder(),
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
