from fastapi import APIRouter

from app.engine.workflow_dependency_modeling.service import (
    get_sample_workflow,
)
from app.shared.schemas.workflow import WorkflowGraph


router = APIRouter(
    prefix="/api/workflows",
    tags=["Workflow Dependency Modeling"],
)


@router.get(
    "/sample",
    response_model=WorkflowGraph,
    response_model_by_alias=True,
)
def read_sample_workflow() -> WorkflowGraph:
    return get_sample_workflow()