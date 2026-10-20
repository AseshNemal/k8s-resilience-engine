from dataclasses import dataclass
from typing import Dict


@dataclass
class ClusterNode:
    name: str
    zone: str | None
    cpu_available: str
    memory_available: str
    labels: Dict[str, str] | None = None
    # Node "Ready" condition; None when the node does not report one.
    ready: bool | None = None
    schedulable: bool = True


@dataclass
class ClusterPod:
    name: str
    node: str | None
    ready: bool
    owner_kind: str | None
    owner_name: str | None
    app_label: str
    labels: Dict[str, str]
    namespace: str = "default"
    restart_count: int = 0
    owner_uid: str | None = None
    phase: str | None = None
