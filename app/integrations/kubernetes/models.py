"""Plain data returned by the Kubernetes integration (no Kubernetes client types leak out)."""

from dataclasses import dataclass
from typing import Dict


@dataclass
class ClusterNode:
    name: str
    zone: str | None
    cpu_available: str | None
    memory_available: str | None
    control_plane: bool = False


@dataclass
class ClusterPod:
    name: str
    node: str | None  # None until the scheduler has assigned a node
    ready: bool
    owner_kind: str | None
    owner_name: str | None
    app_label: str
    labels: Dict[str, str]
    phase: str | None = None
    terminating: bool = False
    restart_count: int | None = None  # None means the cluster did not report container statuses


@dataclass
class ClusterDeployment:
    name: str
    declared_replicas: int


@dataclass
class ClusterService:
    name: str
    selector: Dict[str, str]  # empty for a Service without a selector (it selects no pods)


@dataclass
class ClusterReplicaSet:
    name: str
    deployment_name: str | None  # None unless a Deployment owns this ReplicaSet
