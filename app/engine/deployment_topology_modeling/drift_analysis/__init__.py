"""
Resilience Drift Analysis & Placement Exposure Modeling Module.

This package provides domain detectors, mathematical formulations, and evaluation engines
for comparing declared Kubernetes deployment intent against live runtime pod placement.
"""

from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
    FailureDomainType,
    ExposureLevel,
)
from app.engine.deployment_topology_modeling.drift_analysis.exceptions import (
    DriftAnalysisError,
    ExposureEvaluationError,
    InvalidTopologyConstraintError,
    DriftThresholdExceededError,
)
from app.engine.deployment_topology_modeling.drift_analysis.drift_config import DriftConfig
from app.engine.deployment_topology_modeling.drift_analysis.drift_models import (
    DriftViolation,
    ServiceExposureResult,
    ClusterExposureReport,
    PlacementDriftDiffReport,
    BaselineB2Result,
    ModelAccuracyCertificate,
)

__all__ = [
    "DriftType",
    "DriftSeverity",
    "FailureDomainType",
    "ExposureLevel",
    "DriftAnalysisError",
    "ExposureEvaluationError",
    "InvalidTopologyConstraintError",
    "DriftThresholdExceededError",
    "DriftConfig",
    "DriftViolation",
    "ServiceExposureResult",
    "ClusterExposureReport",
    "PlacementDriftDiffReport",
    "BaselineB2Result",
    "ModelAccuracyCertificate",
]
