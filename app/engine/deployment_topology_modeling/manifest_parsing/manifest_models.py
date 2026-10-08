from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class ResourceLimits(BaseModel):
    cpu: str = "4.0"
    memory: str = "8Gi"

class TopologyConstraint(BaseModel):
    max_skew: int = 1
    topology_key: str = "kubernetes.io/hostname"
    when_unsatisfiable: str = "DoNotSchedule"

class DeclaredIntent(BaseModel):
    """
    Represents the exact constraints and rules the developer requested
    in the YAML file before Kubernetes altered it.
    """
    service_name: str
    expected_replicas: int = Field(default=1, description="Number of replicas originally requested.")
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    spread_constraints: List[TopologyConstraint] = Field(default_factory=list)
    has_node_affinity: bool = False
    
    # Phase 2 Metric Tracking
    is_topology_compliant: bool = True
