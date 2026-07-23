from app.shared.schemas.base import APIModel


class WorkflowEdge(APIModel):
    source: str
    target: str


class WorkflowGraph(APIModel):
    workflow_id: str
    criticality: float
    mandatory_services: list[str]
    optional_services: list[str] = []
    edges: list[WorkflowEdge]