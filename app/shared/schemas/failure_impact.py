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
