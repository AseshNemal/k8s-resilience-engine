from pydantic import Field
from app.shared.schemas.base import APIModel


class NodeModel(APIModel):
    name: str
    zone: str | None = None
    cpu_available: float | None = None
    memory_available: float | None = None


class PodModel(APIModel):
    name: str
    node: str
    zone: str | None = None
    ready: bool
    # None means the cluster did not report container statuses; 0 is an observed zero.
    restart_count: int | None = None


class ClusterMetadata(APIModel):
    failure_domain_type: str = "kubernetes.io/hostname"
    node_names: list[str] = Field(default_factory=list)


class ValidationMetrics(APIModel):
    # Accuracy is reported only when it has been measured against ground truth; None means
    # "not measured". It is never defaulted to a perfect score.
    service_pod_mapping_accuracy: float | None = None
    pod_node_mapping_accuracy: float | None = None
    concentration_detection_accuracy: float | None = None
    runtime_overhead_ms: float = 0.0


class ServicePlacement(APIModel):
    name: str
    declared_replicas: int
    ready_replicas: int
    replica_concentration: float | None = None
    drift_detected: bool = False
    drift_reasons: list[str] = Field(default_factory=list)
    pods: list[PodModel] = Field(default_factory=list)


class DeploymentGraph(APIModel):
    snapshot_id: str = "snapshot-default"
    timestamp: str = ""
    cluster: ClusterMetadata = Field(default_factory=ClusterMetadata)
    validation: ValidationMetrics = Field(default_factory=ValidationMetrics)
    nodes: list[NodeModel] = Field(default_factory=list)
    services: list[ServicePlacement] = Field(default_factory=list)