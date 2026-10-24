"""One recommendation for several workflows at once: the candidate that protects them all, or the ones that matter most.

Member 4 sizes a failure with Component 3's single-workflow predictor, once per workflow, and weights each workflow's outcome by its
criticality. Component 3 is called, never changed. A portfolio evaluation is an analysis: it has its own evidence source, which no
approval, apply or proof path accepts, so a person approves a candidate for one named workflow afterwards.
"""

import json

import pytest

from app.engine.deployment_topology_modeling.service import discover_live
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.recommendation_evaluation_validation import live_pipeline
from app.engine.recommendation_evaluation_validation.declared_workflow import ENV_NAME
from app.engine.recommendation_evaluation_validation.portfolio import (
    PORTFOLIO_ANALYSIS_SOURCE,
    PORTFOLIO_WORKFLOW_ID,
    WorkflowPortfolio,
    predict_portfolio_impact,
)
from app.engine.recommendation_evaluation_validation.portfolio_pipeline import (
    build_declared_portfolio,
    build_frozen_portfolio,
    describe_portfolio,
)
from app.engine.recommendation_evaluation_validation.ranking import CRITICALITY_WEIGHTED
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod

REQUESTS = {"containers": [{"name": "app", "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}}}]}


def two_problem_cluster() -> FakeClusterSource:
    """db sits on w1 and cache on w2 (each alone on one node); web is spread. No node loss breaks both workflows."""
    return FakeClusterSource(
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod(f"db-{i}", "w1", app="db") for i in range(3)]
                      + [pod(f"cache-{i}", "w2", app="cache") for i in range(2)]
                      + [pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"shop": [deployment("db", 3), deployment("cache", 2), deployment("web", 2)]},
        template_specs={"db": REQUESTS, "cache": REQUESTS, "web": REQUESTS})


def graph(workflow_id: str, criticality: float, mandatory: list[str]) -> WorkflowGraph:
    return WorkflowGraph(workflow_id=workflow_id, criticality=criticality, mandatory_services=mandatory,
                         edges=[WorkflowEdge(source=mandatory[0], target=target) for target in mandatory[1:]])


CHECKOUT, BROWSE = graph("checkout", 0.9, ["web", "db"]), graph("browse", 0.3, ["web", "cache"])


@pytest.fixture
def topology(use_cluster):
    use_cluster(two_problem_cluster())
    return discover_live(namespace="shop").graph


def test_a_portfolio_needs_workflows_with_distinct_ids_and_a_criticality_to_weigh_by():
    with pytest.raises(ValueError, match="at least one"):
        WorkflowPortfolio(())
    with pytest.raises(ValueError, match="Duplicate"):
        WorkflowPortfolio((CHECKOUT, CHECKOUT))
    with pytest.raises(ValueError, match="criticality"):
        WorkflowPortfolio((graph("a", 0.0, ["web"]), graph("b", 0.0, ["db"])))


def test_the_blast_radius_of_a_failure_is_the_criticality_share_of_the_workflows_it_breaks(topology):
    portfolio = WorkflowPortfolio((CHECKOUT, BROWSE))

    at_w1 = predict_portfolio_impact(portfolio, topology, "w1", predict_failure_impact)
    at_w2 = predict_portfolio_impact(portfolio, topology, "w2", predict_failure_impact)

    assert at_w1.failed_workflows == ["checkout"]
    assert at_w1.blast_radius == pytest.approx(100 * 0.9 / 1.2, abs=0.01) and at_w1.survivability == pytest.approx(100 - at_w1.blast_radius)
    assert at_w2.failed_workflows == ["browse"] and at_w2.blast_radius == pytest.approx(100 * 0.3 / 1.2, abs=0.01)
    assert at_w1.workflow_id == PORTFOLIO_WORKFLOW_ID


def test_the_per_node_figures_agree_with_the_assessed_node_and_list_the_worst_node_first(topology):
    report = predict_portfolio_impact(WorkflowPortfolio((CHECKOUT, BROWSE)), topology, "w2", predict_failure_impact)

    figures = {risk.node: risk.blast_radius for risk in report.node_risk_ranking}
    assert figures["w2"] == pytest.approx(report.blast_radius, abs=0.01)
    assert [risk.node for risk in report.node_risk_ranking][0] == "w1" and figures["w3"] == 0.0


def test_the_root_causes_of_every_failing_workflow_are_offered_once(topology):
    at_w1 = predict_portfolio_impact(WorkflowPortfolio((CHECKOUT, BROWSE)), topology, "w1", predict_failure_impact)

    assert [(c.type, c.service) for c in at_w1.root_causes] == [(c.type, c.service) for c in
                                                                predict_failure_impact(CHECKOUT, topology, "w1").root_causes]


def test_a_portfolio_of_one_workflow_reports_what_the_single_workflow_predictor_reports(topology):
    single = predict_failure_impact(CHECKOUT, topology, "w1")

    alone = predict_portfolio_impact(WorkflowPortfolio((CHECKOUT,)), topology, "w1", predict_failure_impact)

    assert (alone.blast_radius, alone.failed_services, alone.failed_workflows) == (single.blast_radius, single.failed_services, single.failed_workflows)
    assert {r.node: r.blast_radius for r in alone.node_risk_ranking} == {r.node: r.blast_radius for r in single.node_risk_ranking}


# --- the evaluation: candidates for every workflow, ranked on one weighted basis -------------------------------------------------------

def declared_file(tmp_path, monkeypatch, workflows=(CHECKOUT, BROWSE)):
    path = tmp_path / "declared.json"
    path.write_text(json.dumps({"workflows": [
        {"workflowId": w.workflow_id, "criticality": w.criticality, "declaredBy": "asesh", "mandatoryServices": w.mandatory_services,
         "edges": [{"source": e.source, "target": e.target} for e in w.edges]} for w in workflows]}))
    monkeypatch.setenv(ENV_NAME, str(path))


def test_the_most_critical_failure_is_assessed_by_default_and_its_own_fix_is_the_best_single_change(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    live = build_declared_portfolio("shop")

    assert live.evidence["failedNode"] == "w1" and live.evidence["failedNodeChosen"] == "highest blast radius"
    assert live.evidence["workflowId"] == PORTFOLIO_WORKFLOW_ID and live.evidence["source"] == PORTFOLIO_ANALYSIS_SOURCE
    assert live.evidence["ranking"]["basis"] == CRITICALITY_WEIGHTED
    singles = [c for c in live.report.recommendations if c.type != "COMBINATION" and c.score > 0]
    assert max(singles, key=lambda c: c.score).service == "db"


def test_each_candidate_says_which_workflows_it_saves_and_which_it_leaves_exposed(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    live = build_declared_portfolio("shop", failed_node="w1")
    detail = describe_portfolio(live)

    spread_db = next(c for c in detail["candidates"] if c["service"] == "db" and c["type"] == "TOPOLOGY_SPREAD")
    assert spread_db["workflows"] == {"protected": ["checkout"], "exposed": ["browse"], "exposedAt": {"browse": ["w2"]}}
    assert detail["portfolio"]["workflows"] == [{"workflowId": "checkout", "criticality": 0.9}, {"workflowId": "browse", "criticality": 0.3}]
    assert detail["baselineImpact"]["failedWorkflows"] == ["checkout"]


def test_a_portfolio_can_never_be_approved_and_says_to_choose_a_workflow_for_that(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    detail = describe_portfolio(build_declared_portfolio("shop"))

    assert detail["candidates"] and all(c["approval"]["ready"] is False for c in detail["candidates"])
    assert all("one workflow" in c["approval"]["reason"] for c in detail["candidates"])
    assert detail["outcome"]["state"] == "PORTFOLIO_NOT_APPROVABLE" and detail["evidence"]["source"] == PORTFOLIO_ANALYSIS_SOURCE


def test_a_workflow_whose_services_are_missing_is_reported_and_left_out_not_guessed(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch, (CHECKOUT, graph("reports", 0.5, ["web", "warehouse"])))

    live = build_declared_portfolio("shop")

    assert [w["workflowId"] for w in live.evidence["workflows"]] == ["checkout"]
    assert live.evidence["workflowsNotEvaluated"][0]["workflowId"] == "reports"
    assert live.evidence["workflowsNotEvaluated"][0]["code"] == "TOPOLOGY_INCOMPLETE"


def test_when_no_workflow_can_be_evaluated_it_is_an_explicit_error(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch, (graph("reports", 0.5, ["web", "warehouse"]),))

    with pytest.raises(EvidenceError) as caught:
        build_declared_portfolio("shop")

    assert caught.value.code == "TOPOLOGY_INCOMPLETE"


def test_no_declared_workflows_is_an_explicit_error(use_cluster, monkeypatch):
    use_cluster(two_problem_cluster())
    monkeypatch.delenv(ENV_NAME, raising=False)

    with pytest.raises(EvidenceError) as caught:
        build_declared_portfolio("shop")

    assert caught.value.code == "DECLARED_WORKFLOW_UNAVAILABLE"


def test_the_frozen_portfolio_uses_every_frozen_workflow_through_component_1s_public_interface(use_cluster, monkeypatch):
    use_cluster(two_problem_cluster())
    frozen = {w.workflow_id: w for w in (CHECKOUT, BROWSE)}
    monkeypatch.setattr(workflow_service, "list_dependency_assessments",
                        lambda: [type("A", (), {"workflow_id": wid})() for wid in frozen])
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", lambda wid: type(
        "F", (), {"graph": frozen[wid], "freeze_id": f"f-{wid}", "assessment_id": f"a-{wid}", "frozen_at": None})())

    live = build_frozen_portfolio("shop")

    assert [(w["workflowId"], w["freezeId"]) for w in live.evidence["workflows"]] == [("checkout", "f-checkout"), ("browse", "f-browse")]
    assert live.evidence["source"] == PORTFOLIO_ANALYSIS_SOURCE and live.report.selected_recommendation_id


def test_nothing_frozen_is_an_explicit_error_not_an_empty_all_clear(use_cluster, monkeypatch):
    use_cluster(two_problem_cluster())
    monkeypatch.setattr(workflow_service, "list_dependency_assessments", lambda: [])

    with pytest.raises(EvidenceError) as caught:
        build_frozen_portfolio("shop")

    assert caught.value.code == "RECOMMENDATION_EVIDENCE_UNAVAILABLE"


def test_a_single_workflow_evaluation_is_unchanged_by_the_portfolio_code(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    single = live_pipeline.build_declared_recommendation("checkout", "w1", "shop")

    assert single.evidence["ranking"]["basis"] == "single-workflow" and single.evidence["workflowId"] == "checkout"
    assert single.evidence["source"] == "declared-workflow"


# --- protecting every workflow means fixing what breaks at EVERY node, not only at the assessed one ---------------------------------------

def test_candidates_come_from_the_root_causes_at_every_node_so_a_fix_elsewhere_is_considered(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    live = build_declared_portfolio("shop", failed_node="w1")

    assert {"db", "cache"} <= {c.service for c in live.report.recommendations}


def test_the_pair_that_fixes_both_problems_is_the_candidate_that_protects_every_workflow(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    live = build_declared_portfolio("shop", failed_node="w1")
    detail = describe_portfolio(live)

    top = next(c for c in detail["candidates"] if c["selected"])
    assert sorted(top["service"].split("+")) == ["cache", "db"] and top["type"] == "COMBINATION"
    assert top["coverage"] == {"worstCaseBefore": 75.0, "worstCaseAfter": 0.0, "protectsEveryWorkflow": True}
    single = next(c for c in detail["candidates"] if c["service"] == "db" and c["type"] == "TOPOLOGY_SPREAD")
    assert single["coverage"]["worstCaseAfter"] == 25.0 and single["coverage"]["protectsEveryWorkflow"] is False


def test_a_single_workflow_still_gets_candidates_for_its_own_assessed_failure_only(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    single = live_pipeline.build_declared_recommendation("checkout", "w1", "shop")

    assert {c.service for c in single.report.recommendations} == {"db"}


# --- the route ---------------------------------------------------------------------------------------------------------------------------

from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: E402,F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)


def test_the_portfolio_route_answers_for_all_declared_workflows_with_approval_shut(client, use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    response = client.get("/api/recommendations/portfolio/detail", params={"source": "declared", "namespace": "shop"})

    body = response.json()
    assert response.status_code == 200 and response.headers["X-Evidence-Source"] == PORTFOLIO_ANALYSIS_SOURCE
    assert response.headers["X-Evidence-Approvable"] == "false"
    assert [w["workflowId"] for w in body["portfolio"]["workflows"]] == ["checkout", "browse"]
    assert body["outcome"]["state"] == "PORTFOLIO_NOT_APPROVABLE" and not any(c["approval"]["ready"] for c in body["candidates"])


def test_the_portfolio_route_with_nothing_to_evaluate_is_an_explicit_error(client, use_cluster, monkeypatch):
    use_cluster(two_problem_cluster())
    monkeypatch.delenv(ENV_NAME, raising=False)

    response = client.get("/api/recommendations/portfolio/detail", params={"source": "declared", "namespace": "shop"})

    assert response.status_code == 404 and response.json()["detail"]["code"] == "DECLARED_WORKFLOW_UNAVAILABLE"


def test_a_portfolio_candidate_cannot_be_put_to_either_approval_gate(client, use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", lambda wid: (_ for _ in ()).throw(LookupError("none frozen")))
    body = client.get("/api/recommendations/portfolio/detail", params={"source": "declared", "namespace": "shop"}).json()
    candidate = next(c for c in body["candidates"] if c["selected"])
    params = {"workflow_id": PORTFOLIO_WORKFLOW_ID, "namespace": "shop"}

    production = client.post(f"/api/recommendations/{candidate['id']}/request-approval", params=params,
                             json={"recommendationId": candidate["id"], "proposalVersion": "x", "yamlSnapshot": "x"})
    research = client.post(f"/api/recommendations/declared/{candidate['id']}/request-approval", params=params,
                           json={"recommendationId": candidate["id"], "proposalVersion": "x", "acknowledgedBy": "someone", "acknowledgement": "x"})

    assert production.status_code in (404, 409) and "workflow" in json.dumps(production.json()).lower()
    assert research.status_code == 404 and research.json()["detail"]["code"] == "DECLARED_WORKFLOW_UNAVAILABLE"


# --- a combined fix is reviewed part by part, each part under the workflow it protects --------------------------------------------------

def test_a_combined_fix_lists_its_parts_and_the_workflows_each_part_protects(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    detail = describe_portfolio(build_declared_portfolio("shop", failed_node="w1"))

    pair = next(c for c in detail["candidates"] if c["selected"])
    assert sorted((p["service"], p["type"]) for p in pair["parts"]) == [("cache", "TOPOLOGY_SPREAD"), ("db", "TOPOLOGY_SPREAD")]
    by_service = {p["service"]: p for p in pair["parts"]}
    assert by_service["db"]["protects"] == ["checkout"] and by_service["cache"]["protects"] == ["browse"]
    assert all(p["candidateId"].startswith("rec-all-workflows-") for p in pair["parts"])


def test_a_single_change_is_its_own_only_part(use_cluster, tmp_path, monkeypatch):
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    detail = describe_portfolio(build_declared_portfolio("shop", failed_node="w1"))

    single = next(c for c in detail["candidates"] if c["service"] == "db" and c["type"] == "TOPOLOGY_SPREAD")
    assert single["parts"] == [{"candidateId": single["id"], "service": "db", "type": "TOPOLOGY_SPREAD", "protects": ["checkout"]}]


def test_the_detail_carries_every_field_the_page_reads(use_cluster, tmp_path, monkeypatch):
    """recommendation-portfolio.js reads these; a rename here would silently blank the panel, so the names are pinned."""
    use_cluster(two_problem_cluster())
    declared_file(tmp_path, monkeypatch)

    detail = describe_portfolio(build_declared_portfolio("shop", failed_node="w1"))

    assert {"workflows", "notEvaluated", "note", "basis"} <= set(detail["portfolio"])
    assert all({"workflowId", "criticality"} <= set(w) for w in detail["portfolio"]["workflows"])
    for candidate in detail["candidates"]:
        assert {"id", "type", "service", "feasible", "parts", "workflows", "coverage", "ranking", "cost", "feasibility", "selected"} <= set(candidate)
        assert {"protected", "exposed", "exposedAt"} <= set(candidate["workflows"])
        assert all({"candidateId", "service", "type", "protects"} <= set(part) for part in candidate["parts"])
        assert "explanation" in candidate["feasibility"] and "resource" in candidate["cost"]
    ranked = [c for c in detail["candidates"] if c["ranking"] and c["ranking"]["rank"] is not None]
    assert ranked and all(c["coverage"] and {"worstCaseBefore", "worstCaseAfter", "protectsEveryWorkflow"} <= set(c["coverage"]) for c in ranked)
