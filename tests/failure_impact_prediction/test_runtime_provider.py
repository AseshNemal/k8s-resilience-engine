from types import SimpleNamespace

import pytest

from app.engine.deployment_topology_modeling import service as deployment_service
from app.engine.failure_impact_prediction.fixture_provider import get_demo_inputs
from app.engine.failure_impact_prediction.runtime_provider import (
    RuntimeInputUnavailableError,
    get_runtime_component_inputs,
)
from app.engine.workflow_dependency_modeling import service as workflow_service


def test_runtime_inputs_use_public_component_services(monkeypatch) -> None:
    workflow, deployment = get_demo_inputs()
    calls: dict[str, str] = {}

    def frozen_workflow(workflow_id: str) -> SimpleNamespace:
        calls["workflow_id"] = workflow_id
        return SimpleNamespace(
            graph=workflow,
            freeze_id="freeze-checkout-v1",
            assessment_id="assessment-checkout-v1",
        )

    def live_deployment(namespace: str) -> object:
        calls["namespace"] = namespace
        return deployment

    monkeypatch.setattr(
        workflow_service,
        "get_frozen_workflow_graph",
        frozen_workflow,
    )
    monkeypatch.setattr(
        deployment_service,
        "get_live_deployment_graph",
        live_deployment,
    )

    inputs = get_runtime_component_inputs(" checkout ", " research ")

    assert calls == {
        "workflow_id": "checkout",
        "namespace": "research",
    }
    assert inputs.workflow == workflow
    assert inputs.workflow is not workflow
    assert inputs.deployment == deployment
    assert inputs.deployment is not deployment
    assert inputs.workflow_freeze_id == "freeze-checkout-v1"
    assert inputs.workflow_assessment_id == "assessment-checkout-v1"
    assert inputs.namespace == "research"


@pytest.mark.parametrize(
    ("workflow_id", "namespace", "message"),
    [
        ("", "default", "workflow_id"),
        ("   ", "default", "workflow_id"),
        ("checkout", "", "namespace"),
        ("checkout", "   ", "namespace"),
    ],
)
def test_runtime_inputs_reject_blank_identifiers(
    workflow_id: str,
    namespace: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        get_runtime_component_inputs(workflow_id, namespace)


def test_runtime_inputs_preserve_component_one_lookup_error(monkeypatch) -> None:
    def missing_workflow(workflow_id: str) -> None:
        raise LookupError(f"No frozen graph for {workflow_id}")

    monkeypatch.setattr(
        workflow_service,
        "get_frozen_workflow_graph",
        missing_workflow,
    )

    with pytest.raises(LookupError, match="No frozen graph for checkout"):
        get_runtime_component_inputs("checkout")


def test_runtime_inputs_wrap_component_one_storage_failure(monkeypatch) -> None:
    def unavailable_storage(workflow_id: str) -> None:
        raise RuntimeError("database table is unavailable")

    monkeypatch.setattr(
        workflow_service,
        "get_frozen_workflow_graph",
        unavailable_storage,
    )

    with pytest.raises(
        RuntimeInputUnavailableError,
        match="Component 1 frozen-workflow storage is unavailable",
    ):
        get_runtime_component_inputs("checkout")


def test_runtime_inputs_wrap_component_two_collection_failure(monkeypatch) -> None:
    workflow, _ = get_demo_inputs()

    monkeypatch.setattr(
        workflow_service,
        "get_frozen_workflow_graph",
        lambda workflow_id: SimpleNamespace(
            graph=workflow,
            freeze_id="freeze-checkout-v1",
            assessment_id="assessment-checkout-v1",
        ),
    )

    def unavailable_topology(namespace: str) -> None:
        raise RuntimeError("cluster collection failed")

    monkeypatch.setattr(
        deployment_service,
        "get_live_deployment_graph",
        unavailable_topology,
    )

    with pytest.raises(
        RuntimeInputUnavailableError,
        match="Component 2 deployment topology is unavailable",
    ):
        get_runtime_component_inputs("checkout")
