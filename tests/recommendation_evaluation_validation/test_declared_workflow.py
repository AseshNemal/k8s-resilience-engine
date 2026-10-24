"""The declared workflow: Member 4's own source for a workflow when Component 1 has none, honest about what it is."""

import json

import pytest

from app.engine.recommendation_evaluation_validation import declared_workflow as dw
from app.engine.recommendation_evaluation_validation.declared_workflow import (
    DeclaredWorkflowError,
    get_declared_workflow,
    load_declared_workflows,
)
from app.shared.schemas.workflow import WorkflowGraph

GOOD = {"workflowId": "productpage-view", "criticality": 0.8, "declaredBy": "asesh",
        "mandatoryServices": ["productpage", "details", "reviews"], "optionalServices": ["ratings"],
        "edges": [{"source": "productpage", "target": "details"}, {"source": "productpage", "target": "reviews"},
                  {"source": "reviews", "target": "ratings"}]}


def write(tmp_path, document, name="declared.json"):
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return path


def refused(tmp_path, document, code="DECLARED_WORKFLOW_INVALID"):
    with pytest.raises(DeclaredWorkflowError) as raised:
        load_declared_workflows(write(tmp_path, document))
    assert raised.value.code == code, raised.value
    return raised.value


def test_a_declaration_becomes_the_unchanged_shared_workflow_contract(tmp_path):
    (declared,) = load_declared_workflows(write(tmp_path, GOOD))

    assert isinstance(declared.graph, WorkflowGraph)
    assert declared.graph.workflow_id == "productpage-view" and declared.graph.mandatory_services == ["productpage", "details", "reviews"]
    assert declared.graph.optional_services == ["ratings"] and declared.graph.criticality == pytest.approx(0.8)
    assert [(e.source, e.target) for e in declared.graph.edges][0] == ("productpage", "details")
    assert declared.declared_by == "asesh"


def test_it_says_it_is_declared_not_observed_and_not_approved(tmp_path):
    (declared,) = load_declared_workflows(write(tmp_path, GOOD))

    shown = declared.to_dict()

    assert shown["observed"] is False and shown["approved"] is False and "not observed from traces" in shown["limits"]
    assert shown["sourceName"] == "declared.json" and len(shown["declarationSha256"]) == 64 and str(tmp_path) not in json.dumps(shown)


def test_the_digest_identifies_the_exact_file(tmp_path):
    one = load_declared_workflows(write(tmp_path, GOOD, "a.json"))[0]
    same = load_declared_workflows(write(tmp_path, GOOD, "b.json"))[0]
    other = load_declared_workflows(write(tmp_path, {**GOOD, "criticality": 0.9}, "c.json"))[0]

    assert one.declaration_sha256 == same.declaration_sha256 != other.declaration_sha256


def test_several_workflows_can_share_a_file_and_one_is_looked_up_by_id(tmp_path):
    second = {**GOOD, "workflowId": "rated-view"}
    path = write(tmp_path, {"workflows": [GOOD, second]})

    assert [w.graph.workflow_id for w in load_declared_workflows(path)] == ["productpage-view", "rated-view"]
    assert get_declared_workflow("rated-view", path).graph.workflow_id == "rated-view"
    with pytest.raises(DeclaredWorkflowError) as raised:
        get_declared_workflow("nope", path)
    assert raised.value.code == "DECLARED_WORKFLOW_UNAVAILABLE" and raised.value.http_status == 404


@pytest.mark.parametrize("change", [
    {"workflowId": " "}, {"declaredBy": ""}, {"declaredBy": None}, {"criticality": None}, {"criticality": 1.5}, {"criticality": -0.1},
    {"criticality": True}, {"criticality": float("nan")}, {"mandatoryServices": []}, {"mandatoryServices": ["a", "a"]},
    {"mandatoryServices": ["a", 3]}, {"optionalServices": ["productpage"]},
    {"edges": [{"source": "productpage", "target": "ghost"}]}, {"edges": [{"source": "productpage"}]}, {"edges": "x"},
])
def test_an_incomplete_or_inconsistent_declaration_is_refused_never_defaulted(tmp_path, change):
    document = {**GOOD, **change}
    if change.get("criticality") is None and "criticality" in change:
        del document["criticality"]
        document = {k: v for k, v in document.items() if k != "criticality"}

    refused(tmp_path, document)


def test_criticality_has_no_default(tmp_path):
    without = {k: v for k, v in GOOD.items() if k != "criticality"}

    assert "criticality" in refused(tmp_path, without).message


def test_two_workflows_with_one_id_are_refused(tmp_path):
    refused(tmp_path, {"workflows": [GOOD, GOOD]})


def test_a_file_that_is_missing_unreadable_huge_or_not_json_is_an_error_and_never_an_empty_answer(tmp_path):
    with pytest.raises(DeclaredWorkflowError) as raised:
        load_declared_workflows(tmp_path / "absent.json")
    assert raised.value.code == "DECLARED_WORKFLOW_UNREADABLE" and str(tmp_path) not in raised.value.message

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(DeclaredWorkflowError):
        load_declared_workflows(bad)
    huge = tmp_path / "huge.json"
    huge.write_text(" " * (dw.MAX_BYTES + 1))
    with pytest.raises(DeclaredWorkflowError):
        load_declared_workflows(huge)
    empty = write(tmp_path, {"workflows": []}, "empty.json")
    with pytest.raises(DeclaredWorkflowError):
        load_declared_workflows(empty)


def test_with_no_file_named_there_are_no_declared_workflows(monkeypatch):
    monkeypatch.delenv(dw.ENV_NAME, raising=False)

    assert load_declared_workflows() == [] and dw.declared_workflow_path() is None


def test_the_path_comes_from_the_backends_environment_only(tmp_path, monkeypatch):
    monkeypatch.setenv(dw.ENV_NAME, str(write(tmp_path, GOOD)))

    assert [w.graph.workflow_id for w in load_declared_workflows()] == ["productpage-view"]
    assert dw.declared_workflow_path().name == "declared.json"


def test_the_bookinfo_example_is_a_valid_declaration_and_matches_the_projects_own_bookinfo_workflow():
    from pathlib import Path
    from tests.support.member4_harness import bookinfo_workflow

    example = Path(__file__).resolve().parents[2] / "experiments" / "member4_real_validation" / "declared-workflow.bookinfo.example.json"
    declared_all = load_declared_workflows(example)
    declared, reference = declared_all[0], bookinfo_workflow()

    assert declared.graph.model_dump() == reference.model_dump()
    assert [w.graph.workflow_id for w in declared_all] == ["productpage-view", "book-rating-view", "book-details-lookup"]
    assert all(w.declared_by.startswith("REPLACE") for w in declared_all)      # a person must put their own name to each
