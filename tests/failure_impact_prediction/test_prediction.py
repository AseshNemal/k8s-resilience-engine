import pytest

from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.service import predict_failure_impact


def test_node_one_failure_explains_failed_checkout_workflow() -> None:
    workflow, deployment = get_demo_inputs()

    report = predict_failure_impact(workflow, deployment, "node-1")

    assert report.failed_node == "node-1"
    assert report.failed_services == ["cartservice"]
    assert report.failed_workflows == ["checkout"]
    assert report.blast_radius == pytest.approx(100.0)
    assert report.survivability == pytest.approx(0.0)
    assert report.root_causes[0].type == "REPLICA_CONCENTRATION"
    assert report.root_causes[0].service == "cartservice"
    assert report.root_causes[0].concentration == pytest.approx(1.0)


def test_node_two_failure_keeps_all_mandatory_services_available() -> None:
    workflow, deployment = get_demo_inputs()

    report = predict_failure_impact(workflow, deployment, "node-2")

    assert report.failed_services == []
    assert report.failed_workflows == []
    assert report.blast_radius == pytest.approx(0.0)
    assert report.survivability == pytest.approx(100.0)


def test_optional_service_failure_does_not_fail_workflow() -> None:
    workflow, deployment = get_demo_inputs()

    report = predict_failure_impact(workflow, deployment, "node-3")

    assert "emailservice" in report.failed_services
    assert report.failed_workflows == []
    assert report.blast_radius == pytest.approx(0.0)


def test_node_ranking_is_deterministic() -> None:
    workflow, deployment = get_demo_inputs()

    report = predict_failure_impact(workflow, deployment, "node-1")

    assert [risk.node for risk in report.node_risk_ranking] == [
        "node-1",
        "node-2",
        "node-3",
    ]
    assert [risk.blast_radius for risk in report.node_risk_ranking] == [
        100.0,
        0.0,
        0.0,
    ]


def test_prediction_does_not_modify_component_inputs() -> None:
    workflow, deployment = get_demo_inputs()
    before_workflow = workflow.model_dump()
    before_deployment = deployment.model_dump()

    predict_failure_impact(workflow, deployment, "node-1")

    assert workflow.model_dump() == before_workflow
    assert deployment.model_dump() == before_deployment


def test_unknown_failed_node_is_rejected() -> None:
    workflow, deployment = get_demo_inputs()

    with pytest.raises(ValueError, match="Unknown failed node"):
        predict_failure_impact(workflow, deployment, "node-99")
