from app.engine.simulation import simulate_deployment_change
from app.shared.sample_loader import load_sample_json
from app.shared.schemas.deployment import DeploymentGraph


class MoveFirstPaymentPod:
    def apply(
        self,
        deployment: DeploymentGraph,
    ) -> DeploymentGraph:
        service = next(
            item
            for item in deployment.services
            if item.name == "paymentservice"
        )
        service.pods[0].node = "node-3"
        service.pods[0].zone = "zone-b"
        return deployment


class InvalidMutation:
    def apply(self, deployment: DeploymentGraph):
        return None


def _deployment() -> DeploymentGraph:
    return DeploymentGraph.model_validate(
        load_sample_json("deployment.json")
    )


def test_shared_simulation_does_not_mutate_input() -> None:
    deployment = _deployment()

    result = simulate_deployment_change(
        deployment,
        MoveFirstPaymentPod(),
    )

    assert deployment.services[0].pods[0].node == "node-1"
    assert result.before.services[0].pods[0].node == "node-1"
    assert result.after.services[0].pods[0].node == "node-3"


def test_shared_simulation_rejects_invalid_mutation_result() -> None:
    deployment = _deployment()

    try:
        simulate_deployment_change(deployment, InvalidMutation())
    except TypeError as error:
        assert "DeploymentGraph" in str(error)
    else:
        raise AssertionError("Expected invalid mutation result to fail.")
