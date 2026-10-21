from enum import Enum


class DriftType(str, Enum):
    """Classification of resilience drift between declared intent and live cluster state."""
    SCALE_DRIFT = "SCALE_DRIFT"
    AFFINITY_DRIFT = "AFFINITY_DRIFT"
    TOPOLOGY_SPREAD_DRIFT = "TOPOLOGY_SPREAD_DRIFT"
    # Informational only: missing resource limits are not a placement rule, so this type never
    # affects constraint compliance or drift_detected.
    RESOURCE_CONTENTION_DRIFT = "RESOURCE_CONTENTION_DRIFT"


# Violation types that represent declared placement rules (Study Guide section 12,
# "constraint compliance rate": topology or affinity rules).
PLACEMENT_RULE_DRIFT_TYPES = frozenset({DriftType.AFFINITY_DRIFT, DriftType.TOPOLOGY_SPREAD_DRIFT})

# Violation types that compare declared intent with runtime state.
INTENT_DRIFT_TYPES = PLACEMENT_RULE_DRIFT_TYPES | {DriftType.SCALE_DRIFT}


class DriftSeverity(str, Enum):
    """Severity tier of an individual violation (hard rule vs soft rule)."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FailureDomainType(str, Enum):
    """Standard Kubernetes failure domain label keys."""
    HOSTNAME = "kubernetes.io/hostname"
    ZONE = "topology.kubernetes.io/zone"
    REGION = "topology.kubernetes.io/region"


class ExposureLevel(str, Enum):
    """Descriptive label derived from placement_metrics.ExposureStatus and the ready count."""
    FULL_EXPOSURE = "FULL_EXPOSURE"                      # >1 ready replica, all on one node
    SINGLE_REPLICA_EXPOSURE = "SINGLE_REPLICA_EXPOSURE"  # exactly 1 ready replica
    RESILIENT = "RESILIENT"                              # ready replicas on >1 node
    UNAVAILABLE = "UNAVAILABLE"                          # 0 ready replicas before any failure


# Self-chosen targets for the validation panel; not defined by the TAF, proposal or Study Guide.
TARGET_MAPPING_ACCURACY = 0.98
TARGET_CONCENTRATION_ACCURACY = 0.95
