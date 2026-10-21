from pydantic import BaseModel, Field
from typing import List, Dict, Any


class ResourceLimits(BaseModel):
    cpu: str | None = None
    memory: str | None = None


class TopologyConstraint(BaseModel):
    """A declared topologySpreadConstraint. Fields Kubernetes requires are left None when the
    manifest omits them (and a parse warning is recorded) instead of being invented."""
    max_skew: int | None = None
    topology_key: str | None = None
    when_unsatisfiable: str | None = None
    min_domains: int | None = None
    label_selector: Dict[str, Any] | None = None


class DeclaredIntent(BaseModel):
    """Placement intent declared by one Deployment manifest, before Kubernetes acts on it."""
    # Deployment name (kept as `service_name` for backward compatibility of the API payload).
    service_name: str
    namespace: str = "default"
    expected_replicas: int = Field(default=1, description="spec.replicas; Kubernetes defaults it to 1.")
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    spread_constraints: List[TopologyConstraint] = Field(default_factory=list)
    has_node_affinity: bool = False
    affinity: Dict[str, Any] = Field(default_factory=dict)
    selector: Dict[str, str] = Field(default_factory=dict)
    template_labels: Dict[str, str] = Field(default_factory=dict)
    readiness_probe_configured: bool = False
    pod_anti_affinity_configured: bool = False
    pod_affinity_configured: bool = False
    node_selector: Dict[str, str] = Field(default_factory=dict)
    tolerations: List[Dict[str, Any]] = Field(default_factory=list)
    selector_match_expressions: List[Dict[str, Any]] = Field(default_factory=list)
    node_affinity_rules: Dict[str, Any] = Field(default_factory=dict)
    pod_affinity_rules: Dict[str, Any] = Field(default_factory=dict)
    pod_anti_affinity_rules: Dict[str, Any] = Field(default_factory=dict)
    resource_requests: List[Dict[str, str]] = Field(default_factory=list)
    resource_limits_by_container: List[Dict[str, str]] = Field(default_factory=list)
    parse_warnings: List[str] = Field(default_factory=list)

    # Compliance is measured by drift analysis, never assumed from the manifest.
    is_topology_compliant: bool | None = None


class DeclaredService(BaseModel):
    """A Service manifest: which pods it is declared to select."""
    name: str
    namespace: str = "default"
    selector: Dict[str, str] = Field(default_factory=dict)
