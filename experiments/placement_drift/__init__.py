"""
Placement Drift & Controlled Disruption Package.
Component: Component 2 - Deployment Topology Modeling
Author: P.A.M. Minara (IT23154476)
"""

from experiments.placement_drift.constants import (
    DEFAULT_CHAOS_NAMESPACE,
    PROTECTED_NAMESPACES,
    REQUIRED_EXPERIMENT_NODES,
    DisruptionType,
    ScenarioExecutionMode,
    ScenarioStatus,
)
from experiments.placement_drift.exceptions import (
    ChaosSafetyError,
    PlacementDriftExperimentError,
    ScenarioExecutionError,
    SnapshotPersistenceError,
    StateRollbackError,
)
PlacementDriftError = PlacementDriftExperimentError

from experiments.placement_drift.scenario_models import (
    ClusterSnapshot,
    DisruptionEvent,
    NodeTransitionRecord,
    PodPlacementRecord,
    ScenarioResult,
)
from experiments.placement_drift.safety_guard import SafetyGuard
from experiments.placement_drift.cluster_state_freezer import ClusterStateFreezer
from experiments.placement_drift.snapshot_repository import SnapshotRepository
from experiments.placement_drift.base_scenario import BaseScenario
from experiments.placement_drift.scenario_01_initial_deployment import Scenario01InitialDeployment
from experiments.placement_drift.scenario_02_scale_up_stress import Scenario02ScaleUpStress
from experiments.placement_drift.scenario_03_scale_down_loss import Scenario03ScaleDownLoss
from experiments.placement_drift.scenario_04_rolling_update import Scenario04RollingUpdate
from experiments.placement_drift.scenario_05_pod_deletion_chaos import Scenario05PodDeletionChaos
from experiments.placement_drift.scenario_06_node_cordon import Scenario06NodeCordon
from experiments.placement_drift.scenario_07_node_drain_eviction import Scenario07NodeDrainEviction
from experiments.placement_drift.scenario_08_resource_contention import Scenario08ResourceContention
from experiments.placement_drift.drift_transition_matrix import DriftTransitionMatrix
from experiments.placement_drift.placement_drift_calculator import PlacementDriftCalculator
from experiments.placement_drift.disruption_orchestrator import DisruptionOrchestrator
from experiments.placement_drift.disruption_reporter import DisruptionReporter

__all__ = [
    "DEFAULT_CHAOS_NAMESPACE",
    "PROTECTED_NAMESPACES",
    "REQUIRED_EXPERIMENT_NODES",
    "DisruptionType",
    "ScenarioExecutionMode",
    "ScenarioStatus",
    "PlacementDriftError",
    "ChaosSafetyError",
    "ScenarioExecutionError",
    "SnapshotPersistenceError",
    "PodPlacementRecord",
    "ClusterSnapshot",
    "DisruptionEvent",
    "NodeTransitionRecord",
    "ScenarioResult",
    "SafetyGuard",
    "ClusterStateFreezer",
    "SnapshotRepository",
    "BaseScenario",
    "Scenario01InitialDeployment",
    "Scenario02ScaleUpStress",
    "Scenario03ScaleDownLoss",
    "Scenario04RollingUpdate",
    "Scenario05PodDeletionChaos",
    "Scenario06NodeCordon",
    "Scenario07NodeDrainEviction",
    "Scenario08ResourceContention",
    "DriftTransitionMatrix",
    "PlacementDriftCalculator",
    "DisruptionOrchestrator",
    "DisruptionReporter",
]
