"""The whole-cluster failure overview: every node assessed from one snapshot, with honest workflow handling."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.engine.failure_impact_prediction import workflow_basis
from app.engine.failure_impact_prediction.overview import DECLARED, FROZEN, WorkflowBasis, build_failure_overview
from app.engine.failure_impact_prediction.service import predict_failure_impact
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.main import app
from app.shared.schemas.deployment import DeploymentGraph, NodeModel, PodModel, ServicePlacement
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod, service

client = TestClient(app)


def placement(name: str, *pods: tuple[str, str, bool]) -> ServicePlacement:
    return ServicePlacement(
        name=name, declared_replicas=len(pods), ready_replicas=sum(1 for p in pods if p[2]),
        pods=[PodModel(name=f"{name}-{i}", node=n, ready=ready) for i, (_, n, ready) in enumerate(pods)])


def topology() -> DeploymentGraph:
    return DeploymentGraph(
        nodes=[NodeModel(name="w1"), NodeModel(name="w2"), NodeModel(name="w3")],
        services=[
            placement("web", ("a", "w1", True), ("b", "w2", True), ("c", "w3", True)),   # spread
            placement("db", ("a", "w1", True)),                                           # single replica on w1
            placement("cache", ("a", "w2", True), ("b", "w2", True)),                     # concentrated on w2
            placement("batch", ("a", "w3", False)),                                       # not ready anywhere
        ])


def workflow(workflow_id: str, mandatory: list[str], criticality: float = 0.5, optional: list[str] | None = None) -> WorkflowGraph:
    return WorkflowGraph(workflow_id=workflow_id, criticality=criticality, mandatory_services=mandatory,
                         optional_services=optional or [],
                         edges=[WorkflowEdge(source=mandatory[0], target=name) for name in mandatory[1:]])


def by_node(overview):
    return {row["node"]: row for row in overview["nodes"]}


def test_without_a_workflow_the_answer_is_service_level_and_workflow_numbers_are_unknown_not_zero():
    overview = build_failure_overview(topology(), [])
    assert overview["scope"] == "service"
    w1 = by_node(overview)["w1"]
    assert w1["verdict"] == "service-down" and w1["servicesDown"] == ["db"]
    assert w1["servicesDegraded"] == ["web"]
    assert w1["blastRadius"] is None and w1["survivability"] is None and w1["workflowsLost"] is None
    assert w1["headline"] == "If w1 stops, db becomes unavailable. Workflow impact is unknown without a workflow."


def test_every_node_is_assessed_and_ranked_worst_first_with_a_stable_tie_break():
    overview = build_failure_overview(topology(), [WorkflowBasis(workflow("checkout", ["web", "db"]), FROZEN)])
    assert [row["node"] for row in overview["nodes"]] == ["w1", "w2", "w3"]
    assert [row["rank"] for row in overview["nodes"]] == [1, 2, 3]
    assert [(row["node"], row["verdict"]) for row in overview["nodes"]] == [
        ("w1", "workflow-lost"), ("w2", "service-down"), ("w3", "degraded")]
    assert overview["singlePointsOfFailure"] == ["w1", "w2"]


def test_blast_radius_is_the_existing_share_of_failed_workflows_and_matches_a_single_prediction():
    deployment_graph = topology()
    workflows = [workflow("checkout", ["web", "db"]), workflow("search", ["web", "cache"]), workflow("reports", ["web"])]
    overview = build_failure_overview(deployment_graph, [WorkflowBasis(w, FROZEN) for w in workflows])
    w1, w2, w3 = (by_node(overview)[name] for name in ("w1", "w2", "w3"))
    assert w1["workflowsLost"] == 1 and w1["blastRadius"] == pytest.approx(100 / 3, abs=0.01)
    assert w2["workflowsLost"] == 1 and w2["blastRadius"] == pytest.approx(100 / 3, abs=0.01)
    assert w3["workflowsLost"] == 0 and w3["blastRadius"] == 0
    for row in overview["nodes"]:                      # the single-workflow predictor agrees with every cell
        for cell in row["workflows"]:
            single = predict_failure_impact(next(w for w in workflows if w.workflow_id == cell["workflowId"]),
                                            deployment_graph, row["node"])
            assert (single.blast_radius == 100.0) == (not cell["survives"])
            assert set(single.failed_services) >= set(cell["failedMandatoryServices"])


def test_the_weighted_loss_is_the_criticality_share_the_recommendations_page_uses():
    workflows = [workflow("a", ["web", "db"], 0.9), workflow("b", ["web", "cache"], 0.6), workflow("c", ["web"], 0.4)]
    overview = build_failure_overview(topology(), [WorkflowBasis(w, FROZEN) for w in workflows])
    w1, w2, w3 = (by_node(overview)[name] for name in ("w1", "w2", "w3"))
    assert w1["weightedBlastRadius"] == pytest.approx(0.9 / 1.9 * 100, abs=0.01)     # only "a" needs db, which sits on w1
    assert w2["weightedBlastRadius"] == pytest.approx(0.6 / 1.9 * 100, abs=0.01)     # only "b" needs cache, which sits on w2
    assert w3["weightedBlastRadius"] == 0
    assert w1["blastRadius"] == pytest.approx(100 / 3, abs=0.01), "the plain share is still reported"
    assert build_failure_overview(topology(), [])["nodes"][0]["weightedBlastRadius"] is None


def test_an_optional_service_going_down_does_not_fail_the_workflow():
    overview = build_failure_overview(topology(), [WorkflowBasis(workflow("view", ["web"], optional=["db"]), FROZEN)])
    w1 = by_node(overview)["w1"]
    assert w1["workflowsLost"] == 0 and w1["verdict"] == "service-down"
    assert w1["workflows"][0]["unavailableOptionalServices"] == ["db"]


def test_a_degraded_service_is_not_reported_as_down_and_a_node_with_no_ready_pods_says_so():
    graph = topology()
    graph.nodes.append(NodeModel(name="w4"))
    overview = build_failure_overview(graph, [])
    assert by_node(overview)["w3"]["servicesDegraded"] == ["web"] and by_node(overview)["w3"]["servicesDown"] == []
    assert by_node(overview)["w3"]["verdict"] == "degraded"
    assert by_node(overview)["w4"]["verdict"] == "no-pods"
    batch = next(row for row in by_node(overview)["w1"]["services"] if row["service"] == "batch")
    assert batch["outcome"] == "already_unavailable"


def test_a_workflow_naming_a_service_that_is_not_placed_is_reported_and_takes_no_part():
    overview = build_failure_overview(topology(), [WorkflowBasis(workflow("ghost", ["web", "payments"]), DECLARED, "someone")])
    assert overview["scope"] == "service"
    assert overview["workflowsNotEvaluated"][0]["workflowId"] == "ghost"
    assert by_node(overview)["w1"]["blastRadius"] is None


def test_a_workflow_that_is_already_broken_is_reported_apart_and_no_node_is_blamed_for_it():
    # "batch" has no ready replica anywhere, so the workflow needing it fails with or without any node failing.
    broken, healthy = workflow("nightly", ["web", "batch"]), workflow("checkout", ["web", "db"])
    overview = build_failure_overview(topology(), [WorkflowBasis(broken, FROZEN), WorkflowBasis(healthy, FROZEN)])
    assert overview["workflowsAlreadyBroken"] == [{
        "workflowId": "nightly", "name": None, "kind": FROZEN, "services": ["batch"], "reason": "Not working now: batch has no ready replica."}]
    assert [item["workflowId"] for item in overview["workflowsEvaluated"]] == ["checkout"]
    w3 = by_node(overview)["w3"]
    assert w3["workflowsEvaluated"] == 1 and w3["workflowsLost"] == 0 and w3["blastRadius"] == 0
    assert by_node(overview)["w1"]["blastRadius"] == 100


def test_a_set_of_only_broken_workflows_leaves_workflow_numbers_unknown_not_zero():
    overview = build_failure_overview(topology(), [WorkflowBasis(workflow("nightly", ["web", "batch"]), FROZEN)])
    assert overview["scope"] == "service" and by_node(overview)["w1"]["blastRadius"] is None


def test_placement_marks_single_node_services_before_anything_fails():
    placed = {row["service"]: row for row in build_failure_overview(topology(), [])["services"]}
    assert placed["db"]["singleNode"] and placed["cache"]["singleNode"] and not placed["web"]["singleNode"]


def test_a_topology_without_worker_nodes_is_refused():
    with pytest.raises(ValueError):
        build_failure_overview(DeploymentGraph(nodes=[], services=[]), [])


def test_the_inputs_are_not_mutated():
    graph, flow = topology(), workflow("checkout", ["web", "db"])
    before = (graph.model_dump(), flow.model_dump())
    build_failure_overview(graph, [WorkflowBasis(flow, FROZEN)])
    assert (graph.model_dump(), flow.model_dump()) == before


# ---- which workflows are used --------------------------------------------------------------------------------------------

def discovery_for(graph: DeploymentGraph, incomplete: tuple[str, ...] = ()):
    evidence = {s.name: SimpleNamespace(complete=s.name not in incomplete) for s in graph.services}
    return SimpleNamespace(graph=graph, service_evidence=evidence, cluster=SimpleNamespace(connection_fingerprint="fake-connection"))


def stub_sources(monkeypatch, frozen: list[WorkflowGraph], declared: list[WorkflowGraph], reviewed_declared=False):
    from app.engine.workflow_dependency_modeling import service as workflow_service
    rows = [SimpleNamespace(graph=g, operational_review={"kind": "frozen", "reviewedBy": "Owner", "statement": "Reviewed real definition"}, display_name=None) for g in frozen]
    if reviewed_declared:
        rows += [SimpleNamespace(graph=g, operational_review={"kind": "managed", "reviewedBy": "Owner", "statement": "Reviewed real definition"}, display_name=None) for g in declared]
    monkeypatch.setattr(workflow_service, "list_operational_workflows", lambda *a: rows)
    monkeypatch.setattr(workflow_basis, "_frozen_bases", lambda: ([WorkflowBasis(g, FROZEN) for g in frozen], None))
    monkeypatch.setattr(workflow_basis, "_declared_bases", lambda namespace: ([WorkflowBasis(g, DECLARED, "me") for g in declared], None))


def test_frozen_workflows_are_preferred_and_declared_ones_are_listed_but_not_mixed_in(monkeypatch):
    stub_sources(monkeypatch, [workflow("approved", ["web", "db"])], [workflow("declared", ["web"])])
    candidates, _ = workflow_basis.resolve_workflows(discovery_for(topology()), "shop")
    assert {c.basis.workflow.workflow_id: c.included for c in candidates} == {"approved": True, "declared": False}
    assert workflow_basis.basis_label(candidates)["kind"] == FROZEN


def test_unverified_declarations_are_never_a_fallback_when_no_reviewed_workflow_applies(monkeypatch):
    stub_sources(monkeypatch, [workflow("elsewhere", ["web", "payments"])], [workflow("declared", ["web"])])
    candidates, _ = workflow_basis.resolve_workflows(discovery_for(topology()), "shop")
    assert {c.basis.workflow.workflow_id: (c.applicable, c.included) for c in candidates} == {
        "elsewhere": (False, False), "declared": (False, False)}
    label = workflow_basis.basis_label(candidates)
    assert label["kind"] == "none" and "unknown" in label["text"]


def test_a_workflow_over_an_incompletely_placed_service_is_not_applicable(monkeypatch):
    stub_sources(monkeypatch, [workflow("approved", ["web", "db"])], [])
    candidates, _ = workflow_basis.resolve_workflows(discovery_for(topology(), incomplete=("db",)), "shop")
    assert not candidates[0].applicable and "db" in candidates[0].reason
    assert workflow_basis.basis_label(candidates)["kind"] == "none"


def test_naming_workflows_uses_exactly_those(monkeypatch):
    stub_sources(monkeypatch, [workflow("approved", ["web"])], [workflow("declared", ["web", "db"])])
    candidates, _ = workflow_basis.resolve_workflows(discovery_for(topology()), "shop", ["declared"])
    assert {c.basis.workflow.workflow_id: c.included for c in candidates} == {"approved": False, "declared": False}


# ---- the route, over a fake cluster ---------------------------------------------------------------------------------------

def shop_cluster(namespaces=("shop",)) -> FakeClusterSource:
    pods = {ns: [pod("web-1", "worker-1", app="web"), pod("web-2", "worker-2", app="web"), pod("db-1", "worker-1", app="db")]
            for ns in namespaces}
    return FakeClusterSource(
        nodes=[node("worker-1"), node("worker-2", "zone-b"), node("cp-1", None, control_plane=True)],
        pods=pods, deployments={ns: [deployment("web", 2), deployment("db", 1)] for ns in namespaces},
        services={ns: [service("web", {"app": "web"}), service("db", {"app": "db"})] for ns in namespaces})


def test_the_route_picks_the_only_application_and_reports_the_evidence_it_used(use_cluster, monkeypatch):
    use_cluster(shop_cluster())
    stub_sources(monkeypatch, [], [workflow("checkout", ["web", "db"])], reviewed_declared=True)
    body = client.get("/api/failure-impacts/overview").json()
    assert body["state"] == "ready" and body["namespace"] == "shop" and body["namespaceChoice"] == "only-application"
    assert body["isPrediction"] is True and body["readOnly"] is True
    assert body["basis"]["kind"] == DECLARED and body["topology"]["complete"] is True
    first = body["nodes"][0]
    assert first["node"] == "worker-1" and first["verdict"] == "workflow-lost" and first["servicesDown"] == ["db"]
    assert first["blastRadius"] == 100 and first["workflowsLost"] == 1


def test_the_route_does_not_choose_between_several_applications(use_cluster, monkeypatch):
    use_cluster(shop_cluster(("shop", "other")))
    stub_sources(monkeypatch, [], [])
    body = client.get("/api/failure-impacts/overview").json()
    assert body["state"] == "choose-application" and body["namespaces"] == ["other", "shop"] and body["namespace"] is None
    assert "nodes" not in body


def test_the_route_refuses_an_unknown_namespace_with_a_stable_code(use_cluster, monkeypatch):
    use_cluster(shop_cluster())
    stub_sources(monkeypatch, [], [])
    response = client.get("/api/failure-impacts/overview", params={"namespace": "nope"})
    assert response.status_code == 404 and response.json()["detail"]["code"] == "APPLICATION_NOT_FOUND"


def test_the_route_without_any_workflow_still_answers_at_service_level(use_cluster, monkeypatch):
    use_cluster(shop_cluster())
    stub_sources(monkeypatch, [], [])
    body = client.get("/api/failure-impacts/overview", params={"namespace": "shop"}).json()
    assert body["scope"] == "service" and body["basis"]["kind"] == "none"
    assert body["nodes"][0]["blastRadius"] is None


def test_the_route_is_read_only_and_never_reads_sample_data(use_cluster, monkeypatch):
    use_cluster(shop_cluster())
    stub_sources(monkeypatch, [], [])
    monkeypatch.setattr(workflow_service, "get_sample_workflow", lambda *a, **k: pytest.fail("sample data used"), raising=False)
    assert client.get("/api/failure-impacts/overview").status_code == 200


def test_a_workflows_own_name_travels_with_its_id():
    named = WorkflowBasis(workflow("9e28bd", ["web", "db"]), DECLARED, "me", None, "Checkout page")
    overview = build_failure_overview(topology(), [named])
    assert overview["workflowsEvaluated"][0]["name"] == "Checkout page"
    assert by_node(overview)["w1"]["workflows"][0]["name"] == "Checkout page"
