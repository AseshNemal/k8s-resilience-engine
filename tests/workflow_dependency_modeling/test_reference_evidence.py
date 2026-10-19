"""Graph evaluation scores against an explicitly supplied reference, never a built-in synthetic one."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.engine.workflow_dependency_modeling import dependency_assessment_service as service
from app.engine.workflow_dependency_modeling import fixture_repository
from app.engine.workflow_dependency_modeling.dependency_assessment_repository import (
    DependencyAssessmentNotFoundError,
)
from app.engine.workflow_dependency_modeling.models import ReferenceWorkflow
from app.main import app
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph

client = TestClient(app)
ENDPOINT = "/api/workflows/dependency-assessments/{}/graph-evaluation"


def make_reference(*, synthetic: bool) -> ReferenceWorkflow:
    edge = [{"source": "a", "target": "b"}]
    return ReferenceWorkflow.from_mapping({
        "workflowId": "checkout", "synthetic": synthetic,
        "expectedServices": ["a", "b"], "mandatoryServices": ["a", "b"], "optionalServices": [],
        "expectedEdges": edge, "mandatoryEdges": edge, "optionalEdges": [],
    })


class StubAssessments:
    """Just enough of the assessment repository for an approved graph evaluation."""

    def get_assessment(self, assessment_id):
        if assessment_id != "known":
            raise DependencyAssessmentNotFoundError(assessment_id)
        return SimpleNamespace(workflow_id="checkout", edge_interventions=())

    def get_approval(self, assessment_id):
        graph = WorkflowGraph(workflow_id="checkout", criticality=0.9, mandatory_services=["a", "b"],
                              optional_services=[], edges=[WorkflowEdge(source="a", target="b")])
        return SimpleNamespace(approved_graph=graph, mandatory_edges=())


@pytest.fixture(autouse=True)
def stub_repository_and_no_default_source(monkeypatch):
    monkeypatch.setattr(service, "_default_repository", StubAssessments())
    monkeypatch.setattr(service, "_default_reference_source", None)


@pytest.fixture
def forbid_fixture_reads(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a built-in fixture reference was read")

    monkeypatch.setattr(fixture_repository, "_load_json", refuse)


# ---- no reference source: insufficient evidence ---------------------------------------------------------

def test_without_a_reference_source_evaluation_is_insufficient_evidence(forbid_fixture_reads):
    with pytest.raises(EvidenceError) as caught:
        service.evaluate_approved_dependency_assessment("known")

    assert caught.value.code == "REFERENCE_EVIDENCE_UNAVAILABLE"
    assert caught.value.http_status == 409
    assert "checkout" in caught.value.message


def test_the_http_endpoint_says_so_and_never_reads_a_fixture(forbid_fixture_reads):
    response = client.get(ENDPOINT.format("known"))

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "REFERENCE_EVIDENCE_UNAVAILABLE"
    assert detail["component"] == "workflow_dependency"
    assert "sample_data" not in response.text


def test_an_unknown_assessment_is_still_a_404(forbid_fixture_reads):
    assert client.get(ENDPOINT.format("nope")).status_code == 404


def test_the_service_module_no_longer_imports_the_fixture_repository():
    tree = ast.parse(Path(service.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}

    assert not [name for name in imported if "fixture_repository" in name or "sample_loader" in name]


# ---- an explicitly supplied reference is used, with its provenance -----------------------------------------

def test_an_explicit_source_is_used_and_its_provenance_is_returned():
    source = service.ReferenceSource(name="reviewed reference v3", load=lambda workflow_id: make_reference(synthetic=False))

    result, reference, used = service.evaluate_approved_dependency_assessment_with_reference(
        "known", reference_source=source
    )

    assert result.workflow_reconstruction_accuracy == pytest.approx(1.0)
    assert reference.synthetic is False
    assert used.name == "reviewed reference v3"


def test_the_two_argument_form_keeps_returning_just_the_result():
    source = service.ReferenceSource(name="r", load=lambda workflow_id: make_reference(synthetic=False))

    result = service.evaluate_approved_dependency_assessment("known", reference_source=source)

    assert result.workflow_id == "checkout"


def test_a_source_without_the_workflow_is_insufficient_evidence():
    def missing(workflow_id):
        raise FileNotFoundError(workflow_id)

    source = service.ReferenceSource(name="empty store", load=missing)

    with pytest.raises(EvidenceError) as caught:
        service.evaluate_approved_dependency_assessment("known", reference_source=source)

    assert caught.value.code == "REFERENCE_EVIDENCE_UNAVAILABLE"
    assert "empty store" in caught.value.message


def test_the_http_payload_states_where_the_reference_came_from(monkeypatch):
    monkeypatch.setattr(
        service, "_default_reference_source",
        service.ReferenceSource(name="reviewed reference v3", load=lambda workflow_id: make_reference(synthetic=False)),
    )

    response = client.get(ENDPOINT.format("known"))

    assert response.status_code == 200
    body = response.json()
    assert body["assessmentId"] == "known"
    assert body["referenceProvenance"] == {
        "source": "reviewed reference v3", "workflowId": "checkout", "synthetic": False,
    }


def test_a_synthetic_reference_is_labelled_synthetic(monkeypatch):
    monkeypatch.setattr(
        service, "_default_reference_source",
        service.ReferenceSource(name="reviewed reference v3", load=lambda workflow_id: make_reference(synthetic=True)),
    )

    body = client.get(ENDPOINT.format("known")).json()

    assert body["referenceProvenance"]["synthetic"] is True


# ---- the built-in fixtures remain available to research code, explicitly -----------------------------------

def test_the_fixture_references_are_only_reachable_through_an_explicit_source():
    source = fixture_repository.FIXTURE_REFERENCE_SOURCE

    reference = source.load("checkout")

    assert reference.synthetic is True
    assert "synthetic" in source.name.lower()
    assert service._default_reference_source is None  # nothing wires it in by default


def test_the_synthetic_fixture_source_can_be_injected_for_research():
    result = service.evaluate_approved_dependency_assessment(
        "known", reference_source=fixture_repository.FIXTURE_REFERENCE_SOURCE
    )

    assert result.workflow_id == "checkout"
