from app.engine.recommendation_evaluation_validation.service import (
    simulate_first_recommendation,
)
from app.shared.sample_loader import load_sample_json
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import FailureImpactReport


def test_topology_spread_distributes_payment_replicas() -> None:
    deployment = DeploymentGraph.model_validate(
        load_sample_json("deployment.json")
    )

    failure_impact = FailureImpactReport.model_validate(
        load_sample_json("failure_impact.json")
    )

    simulated = simulate_first_recommendation(
        deployment,
        failure_impact,
    )

    payment = next(
        service
        for service in simulated.services
        if service.name == "paymentservice"
    )

    assigned_nodes = {
        pod.node
        for pod in payment.pods
    }

    assert len(assigned_nodes) == 3