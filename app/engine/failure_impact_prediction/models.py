"""Immutable internal models for Component 3 failure-impact prediction."""

from dataclasses import dataclass
from typing import Literal


NodeType = Literal["WORKFLOW", "SERVICE", "POD", "NODE", "FAILURE_DOMAIN"]
RelationshipType = Literal[
    "REQUIRES_MANDATORY",
    "USES_OPTIONAL",
    "CALLS",
    "HAS_REPLICA",
    "RUNS_ON",
    "IN_FAILURE_DOMAIN",
]


@dataclass(frozen=True)
class CrossLayerNode:
    identifier: str
    node_type: NodeType
    label: str


@dataclass(frozen=True)
class CrossLayerEdge:
    source: str
    target: str
    relationship: RelationshipType


@dataclass(frozen=True)
class CrossLayerPod:
    name: str
    node: str
    zone: str | None
    ready: bool


@dataclass(frozen=True)
class CrossLayerService:
    name: str
    declared_replicas: int
    ready_replicas: int
    replica_concentration: float | None
    pods: tuple[CrossLayerPod, ...]


@dataclass(frozen=True)
class CrossLayerGraph:
    workflow_id: str
    criticality: float
    deployment_snapshot_id: str
    mandatory_services: tuple[str, ...]
    optional_services: tuple[str, ...]
    services: tuple[CrossLayerService, ...]
    nodes: tuple[CrossLayerNode, ...]
    edges: tuple[CrossLayerEdge, ...]
    unresolved_services: tuple[str, ...]

    def service(self, name: str) -> CrossLayerService | None:
        return next((service for service in self.services if service.name == name), None)

    @property
    def node_names(self) -> tuple[str, ...]:
        return tuple(
            node.label for node in self.nodes if node.node_type == "NODE"
        )


@dataclass(frozen=True)
class ServiceReplicaState:
    service: str
    initially_ready_pods: tuple[str, ...]
    lost_ready_pods: tuple[str, ...]
    surviving_ready_pods: tuple[str, ...]


@dataclass(frozen=True)
class NodeFailureSimulation:
    failed_node: str
    service_states: tuple[ServiceReplicaState, ...]


@dataclass(frozen=True)
class ServiceSurvivalPrediction:
    service: str
    survives: bool
    initially_ready_pods: tuple[str, ...]
    lost_ready_pods: tuple[str, ...]
    surviving_ready_pods: tuple[str, ...]
    caused_by_failed_node: bool


@dataclass(frozen=True)
class WorkflowSurvivalPrediction:
    workflow_id: str
    survives: bool
    failed_due_to: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowImpactSummary:
    """Outcome of one workflow within a portfolio failure simulation."""

    workflow_id: str
    survives: bool
    failed_services: tuple[str, ...]
    failed_mandatory_services: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioNodeRisk:
    """Portfolio-wide workflow impact caused by one worker node."""

    node: str
    blast_radius: float
    failed_workflows: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioFailureImpact:
    """Aggregated workflow impact for one assumed worker-node failure."""

    failed_node: str
    total_workflows: int
    failed_workflows: tuple[str, ...]
    surviving_workflows: tuple[str, ...]
    failed_services: tuple[str, ...]
    blast_radius: float
    survivability: float
    workflow_impacts: tuple[WorkflowImpactSummary, ...]
    node_risk_ranking: tuple[PortfolioNodeRisk, ...]
