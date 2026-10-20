from pydantic import Field
from typing import Any
from app.shared.schemas.base import APIModel


class NodeModel(APIModel):
    name: str
    zone: str | None = None
    cpu_available: float | None = None
    memory_available: float | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    ready: bool | None = None
    schedulable: bool = True


class PodModel(APIModel):
    name: str
    node: str
    zone: str | None = None
    # True only when the pod's Ready condition is true and its node is not NotReady.
    ready: bool
    phase: str | None = None
    # None means the cluster did not report container statuses; 0 is an observed zero.
    restart_count: int | None = None
    namespace: str = "default"
    owner_kind: str | None = None
    owner_name: str | None = None
    owner_uid: str | None = None


class TopologySpreadConstraintModel(APIModel):
    max_skew: int | None = None
    topology_key: str | None = None
    when_unsatisfiable: str | None = None
    min_domains: int | None = None
    label_selector: dict[str, Any] | None = None


class DeclaredPlacementIntent(APIModel):
    declared_replicas: int = 1
    selector: dict[str, str] = Field(default_factory=dict)
    readiness_probe_configured: bool = False
    node_affinity_configured: bool = False
    pod_anti_affinity_configured: bool = False
    pod_affinity_configured: bool = False
    node_selector: dict[str, str] = Field(default_factory=dict)
    tolerations: list[dict[str, Any]] = Field(default_factory=list)
    selector_match_expressions: list[dict[str, Any]] = Field(default_factory=list)
    node_affinity_rules: dict[str, Any] = Field(default_factory=dict)
    pod_affinity_rules: dict[str, Any] = Field(default_factory=dict)
    pod_anti_affinity_rules: dict[str, Any] = Field(default_factory=dict)
    topology_spread_constraints: list[TopologySpreadConstraintModel] = Field(default_factory=list)
    resource_requests: list[dict[str, str]] = Field(default_factory=list)
    resource_limits: list[dict[str, str]] = Field(default_factory=list)


class ClusterMetadata(APIModel):
    failure_domain_type: str = "kubernetes.io/hostname"
    node_names: list[str] = Field(default_factory=list)


class ValidationMetrics(APIModel):
    # Accuracy is reported only when it has been measured against ground truth; None means
    # "not measured". It is never defaulted to a perfect score.
    service_pod_mapping_accuracy: float | None = None
    pod_node_mapping_accuracy: float | None = None
    owner_resolution_accuracy: float | None = None
    concentration_detection_accuracy: float | None = None
    # None means the overhead was not measured, it is not an observed zero.
    runtime_overhead_ms: float | None = None


class ServicePlacement(APIModel):
    name: str
    namespace: str = "default"
    declared_replicas: int
    ready_replicas: int
    replica_concentration: float | None = None
    # TAF p.7: all ready replicas share one failure domain. None when no replica is ready.
    concentrated: bool | None = None
    drift_detected: bool = False
    drift_reasons: list[str] = Field(default_factory=list)
    pods: list[PodModel] = Field(default_factory=list)
    hosting_nodes: list[str] = Field(default_factory=list)
    exposed_failure_domains: list[str] = Field(default_factory=list)
    declared_intent: DeclaredPlacementIntent | None = None
    deployment_names: list[str] = Field(default_factory=list)


class DeploymentGraph(APIModel):
    snapshot_id: str = "snapshot-default"
    timestamp: str = ""
    cluster: ClusterMetadata = Field(default_factory=ClusterMetadata)
    validation: ValidationMetrics = Field(default_factory=ValidationMetrics)
    nodes: list[NodeModel] = Field(default_factory=list)
    services: list[ServicePlacement] = Field(default_factory=list)
