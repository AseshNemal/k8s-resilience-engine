"""
Data Models for Phase 4: Controlled Chaos & Disruption Drift Experiments.
Component: Component 2 - Deployment Topology Modeling
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from experiments.placement_drift.constants import (
    DisruptionType,
    ScenarioExecutionMode,
    ScenarioStatus,
)


class PodPlacementRecord(BaseModel):
    """Normalized physical placement record for an individual pod replica."""
    pod_name: str = Field(..., description="Unique name of the pod")
    service_name: str = Field(..., description="Logical microservice owning this pod")
    node_name: str = Field(..., description="Physical worker node hosting the pod")
    zone: str | None = Field(default=None, description="Availability zone label, if the node has one")
    is_ready: bool = Field(default=True, description="Whether the pod passes readiness probes")


class ClusterSnapshot(BaseModel):
    """Timestamped snapshot of cluster pod placement with a SHA-256 content hash."""
    snapshot_id: str = Field(..., description="Unique snapshot identifier (e.g. T0-baseline)")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 UTC timestamp",
    )
    sha256_hash: str = Field(..., description="Cryptographic SHA-256 fingerprint of pod placements")
    namespace: str = Field(default="default", description="Namespace analyzed")
    pods: List[PodPlacementRecord] = Field(default_factory=list, description="All observed pods")
    nodes: List[Dict[str, Any]] = Field(default_factory=list, description="Available worker nodes")
    total_pods: int = Field(default=0, description="Total active pods")
    total_services: int = Field(default=0, description="Total unique services identified")


class DisruptionEvent(BaseModel):
    """Specification of an injected chaos disruption event."""
    event_id: str = Field(..., description="Unique identifier for this disruption event")
    disruption_type: DisruptionType = Field(..., description="Type of disruption injected")
    target_resource: str = Field(..., description="Target service, deployment, or worker node")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Execution parameters")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Timestamp when disruption began",
    )


class NodeTransitionRecord(BaseModel):
    """Markov transition record of pods migrating from node n_i to node n_j."""
    from_node: str = Field(..., description="Origin worker node")
    to_node: str = Field(..., description="Destination worker node")
    pod_count: int = Field(default=0, description="Number of pods that migrated")
    services_affected: List[str] = Field(default_factory=list, description="Services involved")


class ScenarioResult(BaseModel):
    """Comprehensive result of a completed or aborted disruption experiment."""
    scenario_id: str = Field(..., description="ID matching the scenario specification")
    scenario_name: str = Field(..., description="Human-readable title")
    disruption_type: DisruptionType = Field(..., description="Category of disruption")
    status: ScenarioStatus = Field(default=ScenarioStatus.PENDING, description="Final outcome status")
    execution_mode: ScenarioExecutionMode = Field(
        default=ScenarioExecutionMode.DRY_RUN_SIMULATION,
        description="Whether run against live Kubernetes or dry-run simulation",
    )
    topology_source: Literal["scripted", "live"] = Field(
        default="scripted",
        description="'scripted': placements written in the scenario code. 'live': placements taken from the "
                    "connected cluster's real topology and disrupted by a deterministic rule (still a dry run)",
    )
    baseline_snapshot_id: str = Field(..., description="T0 snapshot identifier")
    target_snapshot_id: str = Field(..., description="T1 snapshot identifier")
    baseline_hash: str = Field(default="", description="T0 SHA-256 hash")
    target_hash: str = Field(default="", description="T1 SHA-256 hash")
    d_rate: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Share of services whose exposure status changed; None if nothing was compared",
    )
    delta_e_rate: Optional[float] = Field(default=None, description="E_rate(T1) - E_rate(T0)")
    drifted_services: List[str] = Field(default_factory=list, description="Services whose exposure status changed")
    transitions: List[NodeTransitionRecord] = Field(
        default_factory=list, description="State transition matrix records"
    )
    runtime_ms: float = Field(default=0.0, description="Total scenario execution latency in milliseconds")
    error_message: Optional[str] = Field(default=None, description="Error details if scenario failed")
    rollback_successful: bool = Field(default=True, description="Whether cluster was restored to baseline")
