from typing import Any
from pydantic import BaseModel, Field
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
    ExposureLevel,
)


class DriftViolation(BaseModel):
    """Specific placement, topology, or configuration drift violation detected."""
    drift_type: DriftType
    severity: DriftSeverity
    description: str
    expected: Any = None
    actual: Any = None
    affected_failure_domain: str | None = None


class ServiceExposureResult(BaseModel):
    """
    Formal mathematical exposure evaluation result for a single microservice.
    
    Implements SA(s, {n}) evaluation across all cluster failure domains.
    """
    service_name: str
    total_replicas: int = Field(default=1, ge=0)
    ready_replicas: int = Field(default=1, ge=0)
    replica_concentration: float = Field(default=1.0, ge=0.0, le=1.0)
    exposure_level: ExposureLevel
    exposed_failure_domains: list[str] = Field(default_factory=list)
    partial_loss_domains: dict[str, int] = Field(default_factory=dict)
    violations: list[DriftViolation] = Field(default_factory=list)
    severity_score: float = Field(ge=0.0, le=1.0, default=0.0)


class ClusterExposureReport(BaseModel):
    """
    Cluster-wide failure domain exposure summary across all evaluated microservices.
    """
    timestamp: str
    total_services: int = Field(ge=0)
    exposed_service_count: int = Field(ge=0)
    exposure_rate: float = Field(ge=0.0, le=1.0)
    constraint_compliance_rate: float = Field(ge=0.0, le=1.0)
    mean_concentration: float = Field(ge=0.0, le=1.0)
    services: list[ServiceExposureResult] = Field(default_factory=list)
    runtime_overhead_ms: float = Field(ge=0.0, default=0.0)


class PlacementDriftDiffReport(BaseModel):
    """
    Quantifies placement drift (D_rate) between two timestamped cluster snapshots.
    
    Used to track topology stability across scaling, rollout, and reschedule events.
    """
    baseline_snapshot_id: str
    target_snapshot_id: str
    total_services_compared: int = Field(ge=0)
    drifted_services_count: int = Field(ge=0)
    placement_drift_rate: float = Field(ge=0.0, le=1.0)
    newly_exposed_services: list[str] = Field(default_factory=list)
    remediated_services: list[str] = Field(default_factory=list)
    state_transitions: dict[str, str] = Field(default_factory=dict)


class BaselineB2Result(BaseModel):
    """
    Output model for Deployment-Only Baseline (B2).
    
    Ranks services by pure infrastructure failure risk without workflow context,
    providing the formal baseline against which Member 3 evaluates Hypothesis H2/H3.
    """
    service_rankings: list[dict[str, Any]] = Field(default_factory=list)
    most_vulnerable_service: str | None = None
    most_critical_worker_node: str | None = None
    total_single_points_of_failure: int = Field(ge=0, default=0)


class ModelAccuracyCertificate(BaseModel):
    """
    Academic validation results proving model accuracy against ground-truth benchmarks.
    """
    service_pod_mapping_accuracy: float = Field(ge=0.0, le=1.0)
    pod_node_mapping_accuracy: float = Field(ge=0.0, le=1.0)
    owner_resolution_accuracy: float = Field(ge=0.0, le=1.0)
    concentration_detection_accuracy: float = Field(ge=0.0, le=1.0)
    is_statistically_sound: bool = True
