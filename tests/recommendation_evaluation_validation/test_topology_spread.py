import pytest

from app.engine.recommendation_evaluation_validation.feasibility_analyzer import (
    FeasibilityStatus,
    SchedulingEvidence,
)
from app.engine.recommendation_evaluation_validation.service import (
    evaluate_first_recommendation,
)
from app.shared.sample_loader import load_sample_json
from app.shared.schemas.deployment import DeclaredPlacementIntent, DeploymentGraph
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


def with_scheduling_evidence(
    deployment: DeploymentGraph,
) -> tuple[DeploymentGraph, SchedulingEvidence]:
    """The sample graph carries no node readiness, labels, taints, pod owner kind or declared intent, so on
    its own it cannot establish feasibility. This adds exactly those facts: every node Ready and labelled,
    every pod owned by a ReplicaSet, every service with a small resource request, and no taints."""

    for node in deployment.nodes:
        node.ready, node.labels = True, {"kubernetes.io/hostname": node.name}
    for service in deployment.services:
        for pod in service.pods:
            pod.owner_kind = "ReplicaSet"
        service.declared_intent = DeclaredPlacementIntent(
            declared_replicas=service.declared_replicas,
            selector={"app": service.name},
            resource_requests=[{"cpu": "100m", "memory": "128Mi"}],
        )
    return deployment, SchedulingEvidence(node_taints={n.name: [] for n in deployment.nodes})


def test_the_bare_sample_is_not_confirmed_feasible() -> None:
    deployment, failure_impact = load_inputs()

    result = evaluate_first_recommendation(deployment, failure_impact)

    assert result.feasible is False
    assert result.feasibility_status is FeasibilityStatus.INSUFFICIENT_EVIDENCE
    assert result.simulated_deployment is None
    assert result.concentration_reduction == pytest.approx(0.0)


def test_topology_spread_is_feasible() -> None:
    deployment, failure_impact = load_inputs()
    deployment, evidence = with_scheduling_evidence(deployment)

    result = evaluate_first_recommendation(
        deployment,
        failure_impact,
        evidence,
    )

    assert result.feasible is True
    assert result.feasibility_status is FeasibilityStatus.FEASIBLE
    assert result.simulated_deployment is not None


def test_topology_spread_reduces_concentration() -> None:
    deployment, failure_impact = load_inputs()
    deployment, evidence = with_scheduling_evidence(deployment)

    result = evaluate_first_recommendation(
        deployment,
        failure_impact,
        evidence,
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
    deployment, evidence = with_scheduling_evidence(deployment)

    original_nodes = [
        pod.node
        for service in deployment.services
        for pod in service.pods
    ]

    result = evaluate_first_recommendation(
        deployment,
        failure_impact,
        evidence,
    )
    assert result.simulated_deployment is not None  # the simulation really ran

    nodes_after_simulation = [
        pod.node
        for service in deployment.services
        for pod in service.pods
    ]

    assert nodes_after_simulation == original_nodes