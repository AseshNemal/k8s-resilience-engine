import pytest

from app.engine.failure_impact_prediction.fixture_provider import (
    get_demo_inputs,
    get_demo_portfolio_inputs,
)
from app.engine.failure_impact_prediction.portfolio_impact import (
    predict_portfolio_failure_impact,
)
from app.engine.failure_impact_prediction.service import (
    predict_demo_portfolio_failure_impact,
)


def _two_workflow_portfolio():
    return get_demo_portfolio_inputs()


def test_demo_portfolio_is_explicit_and_uses_one_deployment_snapshot() -> None:
    workflows, deployment = get_demo_portfolio_inputs()

    assert [workflow.workflow_id for workflow in workflows] == [
        "checkout",
        "storefront",
    ]
    assert workflows[1].mandatory_services == ["frontend"]
    assert workflows[1].optional_services == ["emailservice"]
    assert deployment.snapshot_id == "component3-demo-snapshot-v1"


def test_portfolio_aggregates_failed_and_surviving_workflows() -> None:
    workflows, deployment = _two_workflow_portfolio()

    result = predict_portfolio_failure_impact(
        workflows,
        deployment,
        "node-1",
    )

    assert result.failed_node == "node-1"
    assert result.total_workflows == 2
    assert result.failed_workflows == ("checkout",)
    assert result.surviving_workflows == ("storefront",)
    assert result.failed_services == ("cartservice",)
    assert result.blast_radius == pytest.approx(50.0)
    assert result.survivability == pytest.approx(50.0)


def test_portfolio_keeps_optional_service_failure_separate_from_workflow_failure() -> None:
    workflows, deployment = _two_workflow_portfolio()

    result = predict_portfolio_failure_impact(
        workflows,
        deployment,
        "node-3",
    )

    browse_impact = next(
        impact
        for impact in result.workflow_impacts
        if impact.workflow_id == "storefront"
    )
    assert "emailservice" in browse_impact.failed_services
    assert browse_impact.failed_mandatory_services == ()
    assert browse_impact.survives is True
    assert result.failed_workflows == ()
    assert result.blast_radius == pytest.approx(0.0)


def test_portfolio_ranks_nodes_by_aggregate_workflow_impact() -> None:
    workflows, deployment = _two_workflow_portfolio()

    result = predict_portfolio_failure_impact(
        workflows,
        deployment,
        "node-2",
    )

    assert [risk.node for risk in result.node_risk_ranking] == [
        "node-1",
        "node-2",
        "node-3",
    ]
    assert [risk.blast_radius for risk in result.node_risk_ranking] == [
        50.0,
        0.0,
        0.0,
    ]
    assert result.node_risk_ranking[0].failed_workflows == ("checkout",)


def test_demo_portfolio_service_runs_the_real_evaluator() -> None:
    result = predict_demo_portfolio_failure_impact("node-1")

    assert result.total_workflows == 2
    assert result.failed_workflows == ("checkout",)
    assert result.blast_radius == pytest.approx(50.0)


def test_portfolio_rejects_empty_workflow_collection() -> None:
    _, deployment = get_demo_inputs()

    with pytest.raises(ValueError, match="At least one workflow"):
        predict_portfolio_failure_impact((), deployment, "node-1")


def test_portfolio_rejects_duplicate_workflow_ids() -> None:
    workflow, deployment = get_demo_inputs()

    with pytest.raises(ValueError, match="Duplicate workflow IDs: checkout"):
        predict_portfolio_failure_impact(
            (workflow, workflow.model_copy(deep=True)),
            deployment,
            "node-1",
        )


def test_portfolio_rejects_unknown_failed_node() -> None:
    workflows, deployment = _two_workflow_portfolio()

    with pytest.raises(ValueError, match="Unknown failed node"):
        predict_portfolio_failure_impact(
            workflows,
            deployment,
            "node-99",
        )


def test_portfolio_prediction_does_not_modify_inputs() -> None:
    workflows, deployment = _two_workflow_portfolio()
    workflow_snapshots = [workflow.model_dump() for workflow in workflows]
    deployment_snapshot = deployment.model_dump()

    predict_portfolio_failure_impact(workflows, deployment, "node-1")

    assert [workflow.model_dump() for workflow in workflows] == workflow_snapshots
    assert deployment.model_dump() == deployment_snapshot
