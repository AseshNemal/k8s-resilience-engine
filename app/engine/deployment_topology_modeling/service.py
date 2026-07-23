from app.shared.sample_loader import load_sample_json
from app.shared.schemas.deployment import DeploymentGraph


def get_sample_deployment() -> DeploymentGraph:
    data = load_sample_json("deployment.json")
    return DeploymentGraph.model_validate(data)