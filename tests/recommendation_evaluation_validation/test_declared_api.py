"""The routes for a declared workflow: readable, labelled, and with every way to approve it shut."""

import json

import pytest

from app.engine.recommendation_evaluation_validation.declared_workflow import ENV_NAME
from app.engine.workflow_dependency_modeling import service as workflow_service
from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)
from tests.recommendation_evaluation_validation.test_independent_inputs import (
    DECLARATION,
    concentrated_db,
    freeze_checkout,
)

PARAMS = {"workflow_id": "checkout", "namespace": "shop"}
DECLARED, DETAIL, LIVE_DETAIL, CONTEXT = ("/api/recommendations/declared", "/api/recommendations/declared/detail",
                                          "/api/recommendations/live/detail", "/api/recommendations/context")


@pytest.fixture
def declared(tmp_path, monkeypatch):
    path = tmp_path / "declared.json"
    path.write_text(json.dumps(DECLARATION))
    monkeypatch.setenv(ENV_NAME, str(path))


@pytest.fixture
def nothing_frozen(monkeypatch):
    def none(workflow_id):
        raise LookupError("no frozen graph")
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", none)
    monkeypatch.setattr(workflow_service, "list_dependency_assessments", lambda: [])


def test_the_declared_report_says_where_it_came_from_and_that_it_cannot_be_approved(client, use_cluster, declared, nothing_frozen):
    use_cluster(concentrated_db())

    response = client.get(DECLARED, params=PARAMS)

    assert response.status_code == 200 and response.json()["recommendations"]
    assert response.headers["X-Evidence-Source"] == "declared-workflow" and response.headers["X-Evidence-Approvable"] == "false"


def test_every_candidate_of_the_declared_detail_has_approval_switched_off_with_the_reason(client, use_cluster, declared, nothing_frozen):
    use_cluster(concentrated_db())

    detail = client.get(DETAIL, params=PARAMS).json()

    assert detail["candidates"] and all(c["approval"]["ready"] is False and c["approval"]["declared"] is True for c in detail["candidates"])
    assert all("declared" in c["approval"]["reason"] for c in detail["candidates"])
    assert detail["outcome"]["state"] == "DECLARED_NOT_APPROVABLE"
    assert detail["declared"]["observed"] is False and detail["declared"]["approvable"] is False
    assert detail["declared"]["declaredBy"] == "asesh" and detail["evidence"]["source"] == "declared-workflow"
    assert detail["schedulingFacts"]["unavailable"] is None


def test_the_same_candidate_is_ready_for_approval_on_the_frozen_route_so_the_difference_is_the_source(client, monkeypatch, use_cluster, declared):
    use_cluster(concentrated_db())
    freeze_checkout(monkeypatch)

    frozen = client.get(LIVE_DETAIL, params=PARAMS).json()
    declared_detail = client.get(DETAIL, params=PARAMS).json()

    assert any(c["approval"]["ready"] for c in frozen["candidates"])
    assert not any(c["approval"]["ready"] for c in declared_detail["candidates"])
    assert [c["type"] for c in frozen["candidates"]] == [c["type"] for c in declared_detail["candidates"]]


def test_asking_without_a_workflow_for_an_unknown_one_or_with_no_declaration_is_an_explicit_error(client, use_cluster, declared, nothing_frozen, monkeypatch):
    use_cluster(concentrated_db())

    assert client.get(DECLARED, params={"namespace": "shop"}).json()["detail"]["code"] == "RECOMMENDATION_EVIDENCE_UNAVAILABLE"
    unknown = client.get(DECLARED, params={"workflow_id": "nope", "namespace": "shop"})
    assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "DECLARED_WORKFLOW_UNAVAILABLE"
    monkeypatch.delenv(ENV_NAME)
    assert client.get(DECLARED, params=PARAMS).status_code == 404


def test_a_declared_workflow_id_cannot_be_put_to_approval_and_no_record_is_made(client, use_cluster, declared, nothing_frozen, sqlite_url):
    use_cluster(concentrated_db())
    candidate = client.get(DECLARED, params=PARAMS).json()["selectedRecommendationId"]

    refused = client.post(f"/api/recommendations/{candidate}/request-approval", params=PARAMS,
                          json={"recommendationId": candidate, "proposalVersion": "v1"})

    assert refused.status_code in (404, 409) and refused.json()["detail"]["code"] == "WORKFLOW_EVIDENCE_UNAVAILABLE"
    assert client.get(f"/api/recommendations/{candidate}/approval").status_code == 404


# --- the context ----------------------------------------------------------------------------------------------------------------------------

def test_the_context_lists_declared_workflows_apart_from_frozen_ones_and_never_selects_one_for_you(client, use_cluster, declared, nothing_frozen):
    use_cluster(concentrated_db())

    context = client.get(CONTEXT, params={"namespace": "shop"}).json()

    assert context["workflows"] == [] and context["ready"] is False and context["workflowId"] is None
    (item,) = context["declaredWorkflows"]
    assert item["workflowId"] == "checkout" and item["declared"] is True and item["approvable"] is False and item["applicable"] is True
    assert item["freezeId"] is None and item["declaredBy"] == "asesh" and "not observed" in item["limits"]
    entry = next(e for e in context["evidence"] if e["id"] == "declaredWorkflow")
    assert entry["state"] == "available" and "never approved" in entry["detail"]
    # the frozen-workflow line still says what it said: declaring a workflow does not pretend one was frozen
    assert next(e for e in context["evidence"] if e["id"] == "workflow")["state"] == "missing"


def test_without_a_declaration_the_context_is_exactly_what_it_was(client, use_cluster, nothing_frozen, monkeypatch):
    use_cluster(concentrated_db())
    monkeypatch.delenv(ENV_NAME, raising=False)

    context = client.get(CONTEXT, params={"namespace": "shop"}).json()

    assert context["declaredWorkflows"] == [] and context["declaredWorkflowsError"] is None
    assert "declaredWorkflow" not in [e["id"] for e in context["evidence"]]


def test_a_declaration_that_cannot_be_used_is_reported_in_the_context(client, use_cluster, nothing_frozen, tmp_path, monkeypatch):
    use_cluster(concentrated_db())
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    monkeypatch.setenv(ENV_NAME, str(broken))

    context = client.get(CONTEXT, params={"namespace": "shop"}).json()

    assert context["declaredWorkflows"] == [] and context["declaredWorkflowsError"]["code"] == "DECLARED_WORKFLOW_INVALID"
    assert next(e for e in context["evidence"] if e["id"] == "declaredWorkflow")["state"] == "missing"
    assert str(tmp_path) not in json.dumps(context)           # the path of the file is never shown


def test_a_declared_workflow_naming_services_this_application_lacks_is_listed_as_not_usable_here(client, use_cluster, nothing_frozen, tmp_path, monkeypatch):
    use_cluster(concentrated_db())
    other = tmp_path / "other.json"
    other.write_text(json.dumps({**DECLARATION, "mandatoryServices": ["web", "db", "ghost"]}))
    monkeypatch.setenv(ENV_NAME, str(other))

    (item,) = client.get(CONTEXT, params={"namespace": "shop"}).json()["declaredWorkflows"]

    assert item["applicable"] is False and "ghost" in item["reason"]
