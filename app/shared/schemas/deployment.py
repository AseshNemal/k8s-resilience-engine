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
    restart_count: int = 0


class ClusterMetadata(APIModel):
    failure_domain_type: str = "kubernetes.io/hostname"
    node_names: list[str]


class ValidationMetrics(APIModel):
    service_pod_mapping_accuracy: float
    pod_node_mapping_accuracy: float
    concentration_detection_accuracy: float
    runtime_overhead_ms: float


class ServicePlacement(APIModel):
    name: str
    declared_replicas: int
    ready_replicas: int
    replica_concentration: float | None = None
    drift_detected: bool = False
    drift_reasons: list[str] = []
    pods: list[PodModel]


class DeploymentGraph(APIModel):
    snapshot_id: str
    timestamp: str
    cluster: ClusterMetadata
    validation: ValidationMetrics
    nodes: list[NodeModel]
    services: list[ServicePlacement]