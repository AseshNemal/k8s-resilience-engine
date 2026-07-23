from app.shared.sample_loader import load_sample_json
from app.shared.schemas.workflow import WorkflowGraph


def get_sample_workflow() -> WorkflowGraph:
    data = load_sample_json("workflow.json")
    return WorkflowGraph.model_validate(data)