from pydantic import Field

from app.shared.schemas.base import APIModel


class RootCause(APIModel):
    type: str
    service: str
    failure_domain: str
    concentration: float | None = None


class NodeRisk(APIModel):
    node: str
    blast_radius: float


class FailureImpactReport(APIModel):
    workflow_id: str
    failed_node: str
    blast_radius: float
    survivability: float
    failed_services: list[str]
    failed_workflows: list[str]
    root_causes: list[RootCause]
    node_risk_ranking: list[NodeRisk] = []


class DeploymentServiceImpact(APIModel):
    service: str
    ready_before: int
    ready_after: int
    lost_ready: int
    outcome: str


class DeploymentNodeRisk(APIModel):
    node: str
    failed_services: list[str] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    service_failure_rate: float | None = None


class DeploymentFailureImpactReport(APIModel):
    analysis_scope: str = "deployment"
    failed_node: str
    services_evaluated: int
    failed_services: list[str] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    already_unavailable_services: list[str] = Field(default_factory=list)
    service_failure_rate: float | None = None
    service_impacts: list[DeploymentServiceImpact] = Field(default_factory=list)
    node_risk_ranking: list[DeploymentNodeRisk] = Field(default_factory=list)
    workflow_evidence_status: str = "not_used"
    workflow_survival: bool | None = None
    workflow_blast_radius: float | None = None
    workflow_evidence_note: str = (
        "Deployment placement only. Workflow dependencies, workflow survival and workflow "
        "blast radius are unknown without a current operational review of a real workflow definition."
    )


class PortfolioWorkflowImpact(APIModel):
    workflow_id: str
    survives: bool
    failed_services: list[str] = Field(default_factory=list)
    failed_mandatory_services: list[str] = Field(default_factory=list)


class PortfolioNodeRisk(APIModel):
    node: str
    blast_radius: float
    failed_workflows: list[str] = Field(default_factory=list)


class PortfolioFailureImpactReport(APIModel):
    failed_node: str
    total_workflows: int
    failed_workflows: list[str] = Field(default_factory=list)
    surviving_workflows: list[str] = Field(default_factory=list)
    failed_services: list[str] = Field(default_factory=list)
    blast_radius: float
    survivability: float
    workflow_impacts: list[PortfolioWorkflowImpact] = Field(
        default_factory=list
    )
    node_risk_ranking: list[PortfolioNodeRisk] = Field(
        default_factory=list
    )
