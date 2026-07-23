import pytest

from app.engine.recommendation_evaluation_validation.service import (
    evaluate_first_recommendation,
)
from app.shared.sample_loader import load_sample_json
from app.shared.schemas.deployment import DeploymentGraph
from app.shared.schemas.failure_impact import FailureImpactReport


def load_inputs() -> tuple[
    DeploymentGraph,
    FailureImpactReport,
]:
    deployment = DeploymentGraph.model_validate(
        load_sample_json("deployment.json")
    )

    failure_impact = FailureImpactReport.model_validate(
        load_sample_json("failure_impact.json")
    )

    return deployment, failure_impact


def test_topology_spread_is_feasible() -> None:
    deployment, failure_impact = load_inputs()

    result = evaluate_first_recommendation(
        deployment,
        failure_impact,
    )

    assert result.feasible is True
    assert result.simulated_deployment is not None


def test_topology_spread_reduces_concentration() -> None:
    deployment, failure_impact = load_inputs()

    result = evaluate_first_recommendation(
        deployment,
        failure_impact,
    )

    assert result.concentration_before == pytest.approx(
        0.67,
        abs=0.01,
    )

    assert result.concentration_after == pytest.approx(
        0.33,
        abs=0.01,
    )

    assert result.concentration_reduction == pytest.approx(
        0.33,
        abs=0.01,
    )

    assert (
        result.concentration_after
        < result.concentration_before
    )


def test_simulation_does_not_change_original_deployment() -> None:
    deployment, failure_impact = load_inputs()

    original_nodes = [
        pod.node
        for service in deployment.services
        for pod in service.pods
    ]

    evaluate_first_recommendation(
        deployment,
        failure_impact,
    )

    nodes_after_simulation = [
        pod.node
        for service in deployment.services
        for pod in service.pods
    ]

    assert nodes_after_simulation == original_nodes