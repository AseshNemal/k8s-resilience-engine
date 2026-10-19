from enum import Enum


class DriftType(str, Enum):
    """Classification of resilience drift between declared intent and live cluster state."""
    SCALE_DRIFT = "SCALE_DRIFT"
    AFFINITY_DRIFT = "AFFINITY_DRIFT"
    TOPOLOGY_SPREAD_DRIFT = "TOPOLOGY_SPREAD_DRIFT"
    RESOURCE_CONTENTION_DRIFT = "RESOURCE_CONTENTION_DRIFT"


class DriftSeverity(str, Enum):
    """Mathematical severity tier for resilience vulnerabilities."""
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
    """Classification of service vulnerability to a single-node failure."""
    FULL_EXPOSURE = "FULL_EXPOSURE"                  # 0 surviving ready replicas if failed node lost
    SINGLE_REPLICA_EXPOSURE = "SINGLE_REPLICA_EXPOSURE"  # Only 1 replica exists by design, hosted on node
    PARTIAL_EXPOSURE = "PARTIAL_EXPOSURE"            # >0 survive, but lost >= 50% capacity
    RESILIENT = "RESILIENT"                          # Replicas safely spread across failure domains


# Default Scoring Weights for 0.0 - 1.0 Severity Calculation
SEVERITY_WEIGHT_FULL_EXPOSURE = 0.50
SEVERITY_WEIGHT_CONCENTRATION = 0.25
SEVERITY_WEIGHT_SCALE_MISMATCH = 0.15
SEVERITY_WEIGHT_RESOURCE_UNBOUNDED = 0.10

# Academic Target Baselines
TARGET_MAPPING_ACCURACY = 0.95
TARGET_CONCENTRATION_ACCURACY = 0.95
