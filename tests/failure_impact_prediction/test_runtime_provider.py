from types import SimpleNamespace

import pytest

from app.engine.deployment_topology_modeling import service as deployment_service
from app.engine.deployment_topology_modeling.discovery import (
    DiscoveryError,
    DiscoveryWarning,
    UnresolvedItem,
)
from app.engine.failure_impact_prediction.fixture_provider import (
    get_demo_inputs,
    get_demo_portfolio_inputs,
)
from app.engine.failure_impact_prediction.runtime_provider import (
    RuntimeInputUnavailableError,
    get_runtime_component_inputs,
    get_runtime_portfolio_inputs,
)
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.shared.evidence_errors import EvidenceError
from tests.support.evidence import discovery_for


def freeze(workflow):
    return lambda workflow_id: SimpleNamespace(
        graph=workflow, freeze_id="freeze-checkout-v1", assessment_id="assessment-checkout-v1"
    )


def test_runtime_inputs_use_public_component_services(monkeypatch) -> None:
    workflow, deployment = get_demo_inputs()
    calls: dict[str, str] = {}

    def frozen_workflow(workflow_id: str) -> SimpleNamespace:
        calls["workflow_id"] = workflow_id
        return freeze(workflow)(workflow_id)

    def live_discovery(namespace: str):
        calls["namespace"] = namespace
        return discovery_for(deployment, namespace=namespace)

    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", frozen_workflow)
    monkeypatch.setattr(deployment_service, "discover_live", live_discovery)

    inputs = get_runtime_component_inputs(" checkout ", " research ")

    assert calls == {"workflow_id": "checkout", "namespace": "research"}
    assert inputs.workflow == workflow
    assert inputs.workflow is not workflow
    assert inputs.deployment == deployment
    assert inputs.deployment is not deployment
    assert inputs.workflow_freeze_id == "freeze-checkout-v1"
    assert inputs.workflow_assessment_id == "assessment-checkout-v1"
    assert inputs.namespace == "research"
    assert inputs.topology_status == "ok"
    assert inputs.topology_captured_at == "2026-01-01T00:00:00Z"
    assert inputs.topology_warnings == []


@pytest.mark.parametrize(
    ("workflow_id", "namespace", "message"),
    [
        ("", "default", "workflow_id"),
        ("   ", "default", "workflow_id"),
        ("checkout", "", "namespace"),
        ("checkout", "   ", "namespace"),
    ],
)
def test_runtime_inputs_reject_blank_identifiers(workflow_id: str, namespace: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        get_runtime_component_inputs(workflow_id, namespace)


def test_missing_frozen_workflow_is_explicit_workflow_evidence_error(monkeypatch) -> None:
    def missing_workflow(workflow_id: str) -> None:
        raise LookupError(f"No frozen graph for {workflow_id}")

    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", missing_workflow)

    with pytest.raises(LookupError, match="No frozen graph for checkout") as caught:
        get_runtime_component_inputs("checkout")

    assert isinstance(caught.value, EvidenceError)
    assert (caught.value.code, caught.value.http_status) == ("WORKFLOW_EVIDENCE_UNAVAILABLE", 404)


def test_runtime_inputs_wrap_component_one_storage_failure(monkeypatch) -> None:
    def unavailable_storage(workflow_id: str) -> None:
        raise RuntimeError("database table is unavailable")

    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", unavailable_storage)

    with pytest.raises(RuntimeInputUnavailableError, match="Component 1 frozen-workflow storage is unavailable") as caught:
        get_runtime_component_inputs("checkout")

    assert caught.value.http_status == 503


def test_cluster_discovery_failure_propagates_unchanged_and_never_becomes_a_prediction(monkeypatch) -> None:
    workflow, _ = get_demo_inputs()
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", freeze(workflow))

    def not_connected(namespace: str):
        raise DiscoveryError("CLUSTER_NOT_CONNECTED", "No Kubernetes cluster is connected.", http_status=503)

    monkeypatch.setattr(deployment_service, "discover_live", not_connected)

    with pytest.raises(DiscoveryError) as caught:
        get_runtime_component_inputs("checkout")

    assert caught.value.code == "CLUSTER_NOT_CONNECTED"


def test_unexpected_topology_failure_is_wrapped_without_leaking_its_text(monkeypatch) -> None:
    workflow, _ = get_demo_inputs()
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", freeze(workflow))

    def broken(namespace: str):
        raise RuntimeError("token=abc123 leaked")

    monkeypatch.setattr(deployment_service, "discover_live", broken)

    with pytest.raises(RuntimeInputUnavailableError) as caught:
        get_runtime_component_inputs("checkout")

    assert "abc123" not in str(caught.value)
    assert caught.value.http_status == 503


# ---- partial topology must not be treated as verified topology -------------------------------------------

def referenced(workflow):
    return sorted(
        set(workflow.mandatory_services) | set(workflow.optional_services)
        | {e.source for e in workflow.edges} | {e.target for e in workflow.edges}
    )


def test_incomplete_evidence_for_a_workflow_service_is_insufficient_evidence(monkeypatch) -> None:
    workflow, deployment = get_demo_inputs()
    target = workflow.mandatory_services[0]
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", freeze(workflow))
    monkeypatch.setattr(
        deployment_service, "discover_live",
        lambda namespace: discovery_for(
            deployment, status="partial", incomplete={target: ["UNSCHEDULED_PODS"]},
            unresolved=[UnresolvedItem(kind="Pod", name=f"{target}-9", reason="POD_NOT_SCHEDULED",
                                       namespace="default", affects=[target])],
        ),
    )

    with pytest.raises(EvidenceError) as caught:
        get_runtime_component_inputs("checkout")

    assert (caught.value.code, caught.value.http_status) == ("TOPOLOGY_INCOMPLETE", 409)
    services = {s["service"]: s["reasons"] for s in caught.value.details["services"]}
    assert services == {target: ["UNSCHEDULED_PODS"]}
    assert caught.value.details["unresolved"][0]["name"] == f"{target}-9"


def test_a_workflow_service_the_cluster_never_showed_is_insufficient_evidence(monkeypatch) -> None:
    workflow, deployment = get_demo_inputs()
    missing = workflow.mandatory_services[0]
    reduced = deployment.model_copy(deep=True)
    reduced.services = [s for s in reduced.services if s.name != missing]
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", freeze(workflow))
    monkeypatch.setattr(deployment_service, "discover_live", lambda namespace: discovery_for(reduced))

    with pytest.raises(EvidenceError) as caught:
        get_runtime_component_inputs("checkout")

    assert caught.value.code == "TOPOLOGY_INCOMPLETE"
    assert {s["service"]: s["reasons"] for s in caught.value.details["services"]} == {missing: ["SERVICE_NOT_DISCOVERED"]}


def test_partial_evidence_about_unrelated_pods_does_not_block_but_stays_visible(monkeypatch) -> None:
    workflow, deployment = get_demo_inputs()
    stray = UnresolvedItem(kind="Pod", name="batch-1", reason="NO_MATCHING_DEPLOYMENT",
                           namespace="default", affects=[])
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", freeze(workflow))
    monkeypatch.setattr(
        deployment_service, "discover_live",
        lambda namespace: discovery_for(
            deployment, status="partial", unresolved=[stray],
            warnings=[DiscoveryWarning(code="NAMESPACE_EXISTENCE_UNVERIFIED", message="m")],
        ),
    )

    inputs = get_runtime_component_inputs("checkout")

    assert inputs.topology_status == "partial"
    assert [u.name for u in inputs.topology_unresolved] == ["batch-1"]
    assert [w.code for w in inputs.topology_warnings] == ["NAMESPACE_EXISTENCE_UNVERIFIED"]


def test_runtime_portfolio_loads_each_workflow_and_topology_once(monkeypatch) -> None:
    workflows, deployment = get_demo_portfolio_inputs()
    workflows_by_id = {
        workflow.workflow_id: workflow for workflow in workflows
    }
    workflow_calls: list[str] = []
    deployment_calls: list[str] = []

    def frozen_workflow(workflow_id: str) -> SimpleNamespace:
        workflow_calls.append(workflow_id)
        return SimpleNamespace(
            graph=workflows_by_id[workflow_id],
            freeze_id=f"freeze-{workflow_id}-v1",
            assessment_id=f"assessment-{workflow_id}-v1",
        )

    def live_deployment(namespace: str):
        deployment_calls.append(namespace)
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

    inputs = get_runtime_portfolio_inputs(
        [" checkout ", " storefront "],
        " research ",
    )

    assert workflow_calls == ["checkout", "storefront"]
    assert deployment_calls == ["research"]
    assert [workflow.workflow_id for workflow in inputs.workflows] == [
        "checkout",
        "storefront",
    ]
    assert inputs.workflows[0] is not workflows[0]
    assert inputs.deployment == deployment
    assert inputs.deployment is not deployment
    assert [
        provenance.freeze_id
        for provenance in inputs.workflow_provenance
    ] == ["freeze-checkout-v1", "freeze-storefront-v1"]
    assert inputs.namespace == "research"


@pytest.mark.parametrize(
    ("workflow_ids", "message"),
    [
        ([], "At least one workflow_id"),
        (["checkout", "   "], "non-empty strings"),
        (["checkout", " checkout "], "Duplicate workflow IDs"),
    ],
)
def test_runtime_portfolio_rejects_invalid_workflow_selection(
    workflow_ids: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        get_runtime_portfolio_inputs(workflow_ids)


def test_runtime_portfolio_rejects_blank_namespace() -> None:
    with pytest.raises(ValueError, match="namespace"):
        get_runtime_portfolio_inputs(["checkout"], "   ")
