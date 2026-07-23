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