"""The complete workflow set reaches one approval and one Apply/Reset, against test doubles only."""
import pytest

from app.api.recommendations import get_cluster_applier
from app.main import app
from app.engine.recommendation_evaluation_validation.approval import ApprovalService, StaleProposalError
from app.engine.recommendation_evaluation_validation.approval import ResearchApprovalService
from app.engine.recommendation_evaluation_validation.application.live_analyser import ResearchAnalyser
from app.engine.recommendation_evaluation_validation.application.analysis import AnalysisUnavailable
from app.engine.recommendation_evaluation_validation.portfolio_pipeline import build_portfolio_recommendation
from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from app.engine.recommendation_evaluation_validation.validation import ValidationPlan, ValidationRefusedError, TestTarget
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.integrations.kubernetes.validation_gateway import ObservedDeployment
from app.shared.evidence_errors import EvidenceError
from tests.recommendation_evaluation_validation.test_approval_api import client, override_get_db, sqlite_url  # noqa: F401
from tests.recommendation_evaluation_validation.test_portfolio_recommendation import (
    BROWSE, CHECKOUT, declared_file, graph, two_problem_cluster,
)
from tests.recommendation_evaluation_validation.test_research_api import decision, request_body
from tests.recommendation_evaluation_validation.test_research_approval import requested
from tests.support.member4_fake_apply_gateway import FakeApplyGateway
from tests.support.member4_approvals import InMemoryApprovals

PARAMS = {"namespace": "shop", "all_workflows": "true"}
RESEARCH = "/api/recommendations/declared"


@pytest.fixture
def world(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)


def test_whole_combination_gets_one_research_approval_and_apply_reset(client, world, monkeypatch):
    gateway = FakeApplyGateway([ObservedDeployment(name, "shop", str(i + 10), 1, replicas, {"app": name}, {"app": name})
                                for i, (name, replicas) in enumerate((("db", 3), ("cache", 2), ("web", 2)))])
    monkeypatch.setenv("RESILIENCE_ALLOW_CLUSTER_WRITES", "1")
    app.dependency_overrides[get_cluster_applier] = lambda: lambda: gateway
    try:
        response = client.get(f"{RESEARCH}/detail", params=PARAMS)
        assert response.status_code == 200, response.text
        detail = response.json()
        selected = next(c for c in detail["candidates"] if c["selected"])
        assert selected["type"] == "COMBINATION" and len(selected["parts"]) == 2
        assert selected["researchApproval"]["available"] and not selected["approval"]["ready"]
        candidate = selected["id"]
        requested = client.post(f"{RESEARCH}/{candidate}/request-approval", params=PARAMS, json=request_body(candidate))
        assert requested.status_code == 200, requested.text
        assert requested.json()["evaluationSnapshot"]["provenance"]["workflowScope"] == "all"
        approved = client.post(f"{RESEARCH}/{candidate}/approve", json=decision())
        assert approved.status_code == 200, approved.text
        assert not gateway.patches, "Approval applies nothing"
        applied = client.post(f"{RESEARCH}/{candidate}/apply", json={"appliedBy": "asesh"})
        assert applied.status_code == 200, applied.text
        assert {p["metadata"]["name"] for p in gateway.patches} == {"db", "cache"}
        reset = client.post(f"{RESEARCH}/{candidate}/reset", json={"resetBy": "asesh"})
        assert reset.status_code == 200, reset.text
        assert set(gateway.restores) == {"db", "cache"}
    finally:
        app.dependency_overrides.pop(get_cluster_applier, None)


def test_editing_declarations_after_request_makes_the_whole_request_stale(client, world, tmp_path, monkeypatch):
    detail = client.get(f"{RESEARCH}/detail", params=PARAMS).json()
    candidate = detail["report"]["selectedRecommendationId"]
    assert client.post(f"{RESEARCH}/{candidate}/request-approval", params=PARAMS, json=request_body(candidate)).status_code == 200
    declared_file(tmp_path, monkeypatch, (CHECKOUT, BROWSE.model_copy(update={"criticality": 0.4})))
    refused = client.post(f"{RESEARCH}/{candidate}/approve", json=decision())
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"


def test_an_incomplete_workflow_set_is_never_approvable(world, tmp_path, monkeypatch):
    declared_file(tmp_path, monkeypatch, (CHECKOUT, graph("missing", 0.3, ["warehouse"])))
    with pytest.raises(EvidenceError) as error:
        build_portfolio_recommendation("shop", declared=True)
    assert error.value.code == "PORTFOLIO_EVIDENCE_INCOMPLETE"


def test_frozen_portfolio_approval_is_bound_to_every_freeze_even_if_the_effect_is_unchanged(world, monkeypatch):
    version = ["1"]
    workflows = {w.workflow_id: w for w in (CHECKOUT, BROWSE)}
    monkeypatch.setattr(workflow_service, "list_dependency_assessments", lambda: [type("A", (), {"workflow_id": wid})() for wid in workflows])
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", lambda wid: type("F", (), {
        "graph": workflows[wid], "freeze_id": version[0] + wid, "assessment_id": wid, "frozen_at": None})())
    live = build_portfolio_recommendation("shop")
    candidate = live.report.selected_recommendation_id
    service = ApprovalService(InMemoryApprovals())
    service.request_approval(candidate, "v1", live.report, evidence=live.evidence, proposal_yaml=build_yaml_proposal(live, candidate).yaml)
    version[0] = "2"
    with pytest.raises(StaleProposalError):
        service.approve(candidate, "v1", "", decided_by="asesh", evaluate=lambda _: build_portfolio_recommendation("shop"))


def test_post_apply_analysis_recalculates_all_workflows_and_refuses_changed_definitions(world):
    live = build_portfolio_recommendation("shop", "w1", declared=True)
    basis = {"workflowScope": "all", "workflowSetDigest": live.evidence["workflowSetDigest"],
             "declarationDigest": live.evidence["declaredWorkflow"]["declarationSha256"],
             "namespace": "shop", "failedNode": "w1", "targetServices": ["db", "cache"]}
    result = ResearchAnalyser().recalculate(basis)
    assert result["blastRadius"] == 75.0 and result["failedWorkflows"] == ["checkout"]
    with pytest.raises(AnalysisUnavailable):
        ResearchAnalyser().recalculate({**basis, "workflowSetDigest": "changed"})


def test_a_single_workflow_test_cannot_be_presented_as_proof_of_the_entire_workflow_set(world):
    live = build_portfolio_recommendation("shop", declared=True)
    service = ResearchApprovalService(InMemoryApprovals())
    candidate, _ = requested(service, live)
    approval = service.approve(candidate, "v1", "", decided_by="asesh", evaluate=lambda _: live)
    with pytest.raises(ValidationRefusedError) as error:
        ValidationPlan.from_approval(approval, live, CHECKOUT, TestTarget("test", "test"), allow_declared_workflow=True)
    assert error.value.code == "ALL_WORKFLOWS_PROOF_UNAVAILABLE"
