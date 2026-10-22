import pytest

from app.engine.failure_impact_prediction.baseline_comparison import (
    compare_failure_impact_methods,
)
from app.engine.failure_impact_prediction.fixture_provider import (
    get_demo_inputs,
)


def test_comparison_runs_all_methods_for_one_declared_scenario() -> None:
    workflow, deployment = get_demo_inputs()

    comparison = compare_failure_impact_methods(
        workflow,
        deployment,
        "node-1",
        dependency_scenario_services={"cartservice"},
    )

    assert comparison.workflow_id == "checkout"
    assert comparison.deployment_snapshot_id == "component3-demo-snapshot-v1"
    assert comparison.failed_node == "node-1"
    assert comparison.dependency_scenario_services == ("cartservice",)

    assert comparison.replica_count_only.vulnerable_services == (
        "emailservice",
    )
    assert comparison.deployment_only.node_induced_failures == (
        "cartservice",
    )
    assert comparison.dependency_only.failed_mandatory_services == (
        "cartservice",
    )
    assert comparison.dependency_only.survives is False
    assert comparison.proposed_cross_layer.failed_services == [
        "cartservice"
    ]
    assert comparison.proposed_cross_layer.failed_workflows == ["checkout"]
    assert comparison.proposed_cross_layer.blast_radius == pytest.approx(100.0)


def test_comparison_preserves_differences_between_limited_methods() -> None:
    workflow, deployment = get_demo_inputs()

    comparison = compare_failure_impact_methods(
        workflow,
        deployment,
        "node-1",
        dependency_scenario_services={"emailservice"},
    )

    # Replica count sees the single email replica but has no placement context.
    assert comparison.replica_count_only.vulnerable_services == (
        "emailservice",
    )
    # Deployment placement sees cart loss but does not infer workflow impact.
    assert comparison.deployment_only.unavailable_services == (
        "cartservice",
    )
    # Dependency-only analysis sees an optional-service scenario and survives.
    assert comparison.dependency_only.survives is True
    # Only the proposed method joins node placement to mandatory dependencies.
    assert comparison.proposed_cross_layer.failed_workflows == ["checkout"]


def test_comparison_deduplicates_dependency_scenario_deterministically() -> None:
    workflow, deployment = get_demo_inputs()

    comparison = compare_failure_impact_methods(
        workflow,
        deployment,
        "node-2",
        dependency_scenario_services=[
            "unknown-z",
            "emailservice",
            "unknown-z",
            "unknown-a",
        ],
    )

    assert comparison.dependency_scenario_services == (
        "emailservice",
        "unknown-a",
        "unknown-z",
    )
    assert comparison.dependency_only.unreferenced_services == (
        "unknown-a",
        "unknown-z",
    )


def test_comparison_does_not_modify_upstream_inputs() -> None:
    workflow, deployment = get_demo_inputs()
    workflow_before = workflow.model_dump()
    deployment_before = deployment.model_dump()

    compare_failure_impact_methods(
        workflow,
        deployment,
        "node-1",
        dependency_scenario_services={"cartservice"},
    )

    assert workflow.model_dump() == workflow_before
    assert deployment.model_dump() == deployment_before


def test_comparison_rejects_unknown_failed_node() -> None:
    workflow, deployment = get_demo_inputs()

    with pytest.raises(ValueError, match="Unknown failed node: node-99"):
        compare_failure_impact_methods(
            workflow,
            deployment,
            "node-99",
            dependency_scenario_services={"cartservice"},
        )
