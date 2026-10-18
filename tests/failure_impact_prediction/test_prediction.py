import pytest

from app.engine.failure_impact_prediction import service as failure_service
from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.runtime_provider import (
    RuntimeComponentInputs,
)
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
    assert len(report.root_causes) == 1
    assert report.root_causes[0].service == "emailservice"
    assert report.root_causes[0].type == "REPLICA_CONCENTRATION"
    assert report.root_causes[0].concentration == pytest.approx(1.0)


def test_preexisting_service_unavailability_is_explained() -> None:
    workflow, deployment = get_demo_inputs()
    cartservice = next(
        service
        for service in deployment.services
        if service.name == "cartservice"
    )
    cartservice.ready_replicas = 0
    for pod in cartservice.pods:
        pod.ready = False

    report = predict_failure_impact(workflow, deployment, "node-2")

    assert report.failed_services == ["cartservice"]
    assert report.failed_workflows == ["checkout"]
    assert len(report.root_causes) == 1
    assert report.root_causes[0].service == "cartservice"
    assert report.root_causes[0].type == "PREEXISTING_UNAVAILABILITY"
    assert report.root_causes[0].concentration is None


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


def test_runtime_prediction_uses_validated_component_inputs(monkeypatch) -> None:
    workflow, deployment = get_demo_inputs()
    calls: dict[str, str] = {}

    def runtime_inputs(
        workflow_id: str,
        namespace: str,
    ) -> RuntimeComponentInputs:
        calls["workflow_id"] = workflow_id
        calls["namespace"] = namespace
        return RuntimeComponentInputs(
            workflow=workflow,
            deployment=deployment,
            workflow_freeze_id="freeze-checkout-v1",
            workflow_assessment_id="assessment-checkout-v1",
            namespace=namespace,
        )

    monkeypatch.setattr(
        failure_service,
        "get_runtime_component_inputs",
        runtime_inputs,
    )

    report = failure_service.predict_runtime_failure_impact(
        "checkout",
        "node-1",
        "research",
    )

    assert calls == {
        "workflow_id": "checkout",
        "namespace": "research",
    }
    assert report.failed_node == "node-1"
    assert report.failed_workflows == ["checkout"]
