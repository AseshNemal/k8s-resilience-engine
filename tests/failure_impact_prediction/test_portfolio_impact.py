import pytest

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.portfolio_impact import (
    predict_portfolio_failure_impact,
)
from app.shared.schemas.workflow import WorkflowEdge


def _two_workflow_portfolio():
    checkout, deployment = get_demo_inputs()
    browsing = checkout.model_copy(deep=True)
    browsing.workflow_id = "browse-catalog"
    browsing.mandatory_services = ["frontend"]
    browsing.optional_services = ["emailservice"]
    browsing.edges = [
        WorkflowEdge(source="frontend", target="emailservice")
    ]
    return (checkout, browsing), deployment


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
    assert result.surviving_workflows == ("browse-catalog",)
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
        if impact.workflow_id == "browse-catalog"
    )
    assert "emailservice" in browse_impact.failed_services
    assert browse_impact.failed_mandatory_services == ()
    assert browse_impact.survives is True
    assert result.failed_workflows == ()
    assert result.blast_radius == pytest.approx(0.0)


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
