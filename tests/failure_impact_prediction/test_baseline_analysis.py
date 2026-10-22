import pytest

from app.engine.failure_impact_prediction.baseline_analysis import (
    analyze_dependency_only,
    analyze_deployment_only,
    analyze_replica_count_only,
)
from app.engine.failure_impact_prediction.fixture_provider import (
    get_demo_inputs,
)


def test_replica_count_baseline_ignores_node_placement() -> None:
    _, deployment = get_demo_inputs()

    result = analyze_replica_count_only(deployment)

    assert result.deployment_snapshot_id == "component3-demo-snapshot-v1"
    assert result.unavailable_services == ()
    assert result.single_replica_services == ("emailservice",)
    assert result.vulnerable_services == ("emailservice",)
    assert result.vulnerable_service_percentage == pytest.approx(20.0)


def test_replica_count_baseline_distinguishes_preexisting_unavailability() -> None:
    _, deployment = get_demo_inputs()
    cart = next(
        service for service in deployment.services if service.name == "cartservice"
    )
    cart.ready_replicas = 0

    result = analyze_replica_count_only(deployment)

    assert result.unavailable_services == ("cartservice",)
    assert result.single_replica_services == ("emailservice",)
    assert result.vulnerable_services == ("cartservice", "emailservice")
    assert result.vulnerable_service_percentage == pytest.approx(40.0)


def test_replica_count_baseline_requires_service_evidence() -> None:
    _, deployment = get_demo_inputs()
    deployment.services = []

    with pytest.raises(ValueError, match="At least one service"):
        analyze_replica_count_only(deployment)


def test_deployment_baseline_finds_services_lost_with_selected_node() -> None:
    _, deployment = get_demo_inputs()

    result = analyze_deployment_only(deployment, "node-1")

    assert result.failed_node == "node-1"
    assert result.node_induced_failures == ("cartservice",)
    assert result.preexisting_unavailable_services == ()
    assert result.unavailable_services == ("cartservice",)
    assert result.unavailable_service_percentage == pytest.approx(20.0)


def test_deployment_baseline_separates_preexisting_unavailability() -> None:
    _, deployment = get_demo_inputs()
    payment = next(
        service for service in deployment.services if service.name == "paymentservice"
    )
    payment.ready_replicas = 0
    for pod in payment.pods:
        pod.ready = False

    result = analyze_deployment_only(deployment, "node-1")

    assert result.node_induced_failures == ("cartservice",)
    assert result.preexisting_unavailable_services == ("paymentservice",)
    assert result.unavailable_services == ("cartservice", "paymentservice")
    assert result.unavailable_service_percentage == pytest.approx(40.0)


def test_deployment_baseline_rejects_unknown_node() -> None:
    _, deployment = get_demo_inputs()

    with pytest.raises(ValueError, match="Unknown failed node: node-99"):
        analyze_deployment_only(deployment, "node-99")


def test_dependency_baseline_fails_only_for_mandatory_services() -> None:
    workflow, _ = get_demo_inputs()

    optional_result = analyze_dependency_only(workflow, {"emailservice"})
    mandatory_result = analyze_dependency_only(
        workflow,
        {"cartservice", "unknown-service"},
    )

    assert optional_result.survives is True
    assert optional_result.failed_mandatory_services == ()
    assert optional_result.unavailable_optional_services == ("emailservice",)
    assert mandatory_result.survives is False
    assert mandatory_result.failed_mandatory_services == ("cartservice",)
    assert mandatory_result.unreferenced_services == ("unknown-service",)


def test_dependency_baseline_is_deterministic_for_duplicate_inputs() -> None:
    workflow, _ = get_demo_inputs()

    result = analyze_dependency_only(
        workflow,
        ["unknown-z", "cartservice", "unknown-z", "unknown-a"],
    )

    assert result.unavailable_services == (
        "cartservice",
        "unknown-a",
        "unknown-z",
    )
    assert result.unreferenced_services == ("unknown-a", "unknown-z")
