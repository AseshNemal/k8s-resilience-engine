from typing import Any
from pydantic import BaseModel, Field
from app.engine.deployment_topology_modeling.drift_analysis.constants import (
    DriftType,
    DriftSeverity,
    ExposureLevel,
)
from app.engine.deployment_topology_modeling.placement_metrics import ExposureStatus


class DriftViolation(BaseModel):
    """Specific placement, topology, or configuration drift violation detected."""
    drift_type: DriftType
    severity: DriftSeverity
    description: str
    expected: Any = None
    actual: Any = None
    affected_failure_domain: str | None = None


class ServiceExposureResult(BaseModel):
    """Single-node failure exposure of one service, i.e. SA(s, {n}) for every node n."""
    service_name: str
    total_replicas: int = Field(default=0, ge=0)
    ready_replicas: int = Field(default=0, ge=0)
    exposure_status: ExposureStatus
    exposure_level: ExposureLevel
    concentrated: bool | None = None
    replica_concentration: float | None = Field(default=None, ge=0.0, le=1.0)
    exposed_failure_domains: list[str] = Field(default_factory=list)
    ready_replicas_per_node: dict[str, int] = Field(default_factory=dict)
    has_placement_rules: bool = False
    placement_rules_satisfied: bool | None = None
    violations: list[DriftViolation] = Field(default_factory=list)


class ClusterExposureReport(BaseModel):
    """Cluster-wide service-level exposure summary.

    Rates are None when their denominator is empty, never a substituted 0.0 or 1.0.
    """
    timestamp: str
    total_services: int = Field(ge=0)
    evaluated_services: int = Field(ge=0)
    unavailable_services: list[str] = Field(default_factory=list)
    exposed_services: list[str] = Field(default_factory=list)
    exposed_service_count: int = Field(ge=0)
    exposure_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    services_with_placement_rules: int = Field(default=0, ge=0)
    constraint_compliance_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_concentration: float | None = Field(default=None, ge=0.0, le=1.0)
    services: list[ServiceExposureResult] = Field(default_factory=list)
    runtime_overhead_ms: float = Field(ge=0.0, default=0.0)


class PlacementDriftDiffReport(BaseModel):
    """Placement drift between two snapshots, using exposure status per service."""
    baseline_snapshot_id: str
    target_snapshot_id: str
    total_services_compared: int = Field(ge=0)
    drifted_services_count: int = Field(ge=0)
    placement_drift_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    drifted_services: list[str] = Field(default_factory=list)
    newly_exposed_services: list[str] = Field(default_factory=list)
    remediated_services: list[str] = Field(default_factory=list)
    added_services: list[str] = Field(default_factory=list)
    removed_services: list[str] = Field(default_factory=list)
    state_transitions: dict[str, str] = Field(default_factory=dict)


class BaselineB2Result(BaseModel):
    """Deployment-Only Baseline (B2): service exposure from Kubernetes placement data only.

    Ranking rule (lexicographic, no weights): exposed services first, then not-exposed,
    then unavailable; within a group higher concentration ratio first, then fewer ready
    replicas, then service name.
    """
    service_rankings: list[dict[str, Any]] = Field(default_factory=list)
    most_vulnerable_service: str | None = None
    most_critical_worker_node: str | None = None
    exposed_services_by_node: dict[str, list[str]] = Field(default_factory=dict)
    total_single_points_of_failure: int = Field(ge=0, default=0)


class ModelAccuracyCertificate(BaseModel):
    """Accuracy of the topology model against labelled ground truth (None = not measured)."""
    service_pod_mapping_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    pod_node_mapping_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    owner_resolution_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    concentration_detection_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    exposure_detection_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    runtime_overhead_ms: float | None = Field(default=None, ge=0.0)
    is_statistically_sound: bool = False
