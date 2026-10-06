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


class ServicePlacement(APIModel):
    name: str
    declared_replicas: int
    ready_replicas: int
    replica_concentration: float | None = None
    drift_detected: bool = False
    drift_reasons: list[str] = []
    pods: list[PodModel]


class DeploymentGraph(APIModel):
    nodes: list[NodeModel]
    services: list[ServicePlacement]