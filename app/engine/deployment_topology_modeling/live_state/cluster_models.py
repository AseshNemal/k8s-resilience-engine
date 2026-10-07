from dataclasses import dataclass
from typing import Dict

@dataclass
class ClusterNode:
    name: str
    zone: str
    cpu_available: str
    memory_available: str

@dataclass
class ClusterPod:
    name: str
    node: str
    ready: bool
    owner_kind: str | None
    owner_name: str | None
    app_label: str
    labels: Dict[str, str]
