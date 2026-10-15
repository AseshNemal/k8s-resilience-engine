"""
Constants and Enums for Phase 4: Controlled Chaos & Disruption Drift Experiments.
Component: Component 2 - Deployment Topology Modeling
"""
from enum import Enum


class DisruptionType(str, Enum):
    """Catalog of the 8 controlled Kubernetes disruption scenarios from the Study Guide."""
    INITIAL_DEPLOYMENT = "INITIAL_DEPLOYMENT"
    SCALE_UP_STRESS = "SCALE_UP_STRESS"
    SCALE_DOWN_LOSS = "SCALE_DOWN_LOSS"
    ROLLING_UPDATE_CHURN = "ROLLING_UPDATE_CHURN"
    POD_DELETION_CHAOS = "POD_DELETION_CHAOS"
    NODE_CORDON = "NODE_CORDON"
    NODE_DRAIN_EVICTION = "NODE_DRAIN_EVICTION"
    RESOURCE_CONTENTION = "RESOURCE_CONTENTION"


class ScenarioExecutionMode(str, Enum):
    """Operating mode for chaos experiments."""
    LIVE_CLUSTER = "LIVE_CLUSTER"
    DRY_RUN_SIMULATION = "DRY_RUN_SIMULATION"


class ScenarioStatus(str, Enum):
    """Lifecycle status of a disruption experiment."""
    PENDING = "PENDING"
    PREPARING = "PREPARING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    ROLLING_BACK = "ROLLING_BACK"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED_BY_SAFETY = "ABORTED_BY_SAFETY"


# Safety Guardrails
PROTECTED_NAMESPACES = frozenset({
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "default",
})

DEFAULT_CHAOS_NAMESPACE = "resilience-chaos-test"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_STABILIZATION_WAIT_MS = 1500
MAX_ALLOWABLE_DRAIN_TIME_SECONDS = 120
REQUIRED_EXPERIMENT_NODES = 2

