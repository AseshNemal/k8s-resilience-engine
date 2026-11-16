"""The product assessment composes one snapshot. It does not invent a workflow or allow Apply."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.engine.failure_impact_prediction import workflow_basis
from app.engine.failure_impact_prediction.overview import DECLARED, WorkflowBasis
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.main import app
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph
from tests.recommendation_evaluation_validation.test_independent_inputs import REQUESTS, concentrated_db
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod, service

client = TestClient(app)


def workflow(workflow_id, mandatory):
    return WorkflowGraph(
        workflow_id=workflow_id, criticality=0.5, mandatory_services=mandatory, optional_services=[],
        edges=[WorkflowEdge(source=mandatory[0], target=name) for name in mandatory[1:]],
    )


def shop_cluster(namespaces=("shop",)):
    pods = {ns: [pod("web-1", "worker-1", app="web"), pod("web-2", "worker-2", app="web"), pod("db-1", "worker-1", app="db")]
            for ns in namespaces}
    return FakeClusterSource(
        nodes=[node("worker-1"), node("worker-2", "zone-b"), node("cp-1", None, control_plane=True)],
        pods=pods, deployments={ns: [deployment("web", 2), deployment("db", 1)] for ns in namespaces},
        services={ns: [service("web", {"app": "web"}), service("db", {"app": "db"})] for ns in namespaces})


def stub_sources(monkeypatch, graphs, *, reviewed=False):
    rows = [SimpleNamespace(graph=graph, operational_review={"kind": "managed", "reviewedBy": "Owner", "statement": "Reviewed"}, display_name=None)
            for graph in graphs] if reviewed else []
    monkeypatch.setattr(workflow_service, "list_operational_workflows", lambda *args: rows)
    monkeypatch.setattr(workflow_basis, "_declared_bases", lambda namespace: ([WorkflowBasis(graph, DECLARED, "me") for graph in graphs], None))
    monkeypatch.setattr(workflow_basis, "_frozen_bases", lambda: ([], None))


def test_one_application_without_a_reviewed_workflow_stays_at_service_level(use_cluster, monkeypatch):
    use_cluster(concentrated_db())
    stub_sources(monkeypatch, [workflow("checkout", ["web", "db"])], reviewed=False)
    body = client.get("/api/assessment").json()
    assert body["state"] == "ready" and body["namespace"] == "shop"
    assert body["evidence"]["workflow"] == "none"
    assert body["risk"]["blastRadius"] is None and body["risk"]["scope"] == "service"
    assert body["vulnerableServices"][0]["service"] == "db"
    recommendation = body["recommendation"]
    assert recommendation["mode"] == "deployment", recommendation
    assert recommendation["approvalAllowed"] is False
    assert recommendation.get("best"), (recommendation.get("code"), recommendation.get("note"), recommendation.get("considered"))
    assert recommendation["best"]["feasible"] is True
    assert "unknown" in body["recommendation"]["note"].lower() or "Workflow" in body["recommendation"]["note"]
    assert body["continuation"]["href"] == "/components/recommendations"
    assert body["continuation"]["failedNode"]
    assert body["continuation"]["candidate"]["service"] == body["recommendation"]["best"]["service"]
    assert body["continuation"]["candidate"]["type"] == body["recommendation"]["best"]["type"]


def test_a_reviewed_workflow_is_scored_and_still_cannot_be_applied_from_here(use_cluster, monkeypatch):
    use_cluster(concentrated_db())
    stub_sources(monkeypatch, [workflow("checkout", ["web", "db"])], reviewed=True)
    body = client.get("/api/assessment").json()
    assert body["evidence"]["workflow"] == DECLARED
    assert body["risk"]["scope"] == "workflow" and body["risk"]["blastRadius"] == 100
    assert body["evidence"]["workflowsEvaluated"][0]["workflowId"] == "checkout"
    recommendation = body["recommendation"]
    assert recommendation["mode"] == "workflow", recommendation
    assert recommendation["approvalAllowed"] is False
    assert recommendation.get("best"), (recommendation.get("code"), recommendation.get("note"), recommendation.get("considered"))
    assert recommendation["best"]["service"] == "db"


def test_several_applications_are_not_chosen_automatically(use_cluster, monkeypatch):
    use_cluster(shop_cluster(("shop", "other")))
    stub_sources(monkeypatch, [], reviewed=False)
    body = client.get("/api/assessment").json()
    assert body["state"] == "choose-application" and body["recommendation"] is None
    chosen = client.get("/api/assessment", params={"namespace": "shop"}).json()
    assert chosen["namespace"] == "shop" and chosen["state"] == "ready"


def one_service_per_worker():
    """Three single-replica services, each alone on its own worker, plus one spread service (the Bookinfo shape)."""
    names = {"details": "worker-1", "ratings": "worker-2", "page": "worker-3"}
    pods = [pod(f"{name}-1", where, app=name) for name, where in names.items()]
    pods += [pod(f"reviews-{i}", f"worker-{i}", app="reviews") for i in (1, 2, 3)]
    return FakeClusterSource(
        nodes=[node("worker-1"), node("worker-2", "zone-b"), node("worker-3", "zone-c"), node("cp-1", None, control_plane=True)],
        pods={"shop": pods},
        deployments={"shop": [*(deployment(name, 1) for name in names), deployment("reviews", 3)]},
        services={"shop": [service(name, {"app": name}) for name in (*names, "reviews")]},
        template_specs={name: REQUESTS for name in (*names, "reviews")})


def test_service_level_answer_is_consistent_across_the_page_and_the_recommendation(use_cluster, monkeypatch):
    use_cluster(one_service_per_worker())
    stub_sources(monkeypatch, [], reviewed=False)
    body = client.get("/api/assessment").json()
    recommendation = body["recommendation"]
    best = recommendation["best"]
    # The headline worker, the worker the change protects, and the worker handed to Recommendations are one and the same.
    assert recommendation["failedNode"] == body["risk"]["worstNode"] == body["continuation"]["failedNode"]
    # A change that removes the same predicted risk for more resources never leads.
    assert all(option["cost"] != "HIGH" for option in [best, *recommendation["alternatives"]]) or best["cost"] == "HIGH"
    assert best["cost"] in ("LOW", "MEDIUM")
    assert all(option["service"] == best["service"] for option in recommendation["alternatives"])
    # One service fixed does not fix the others: the rest are named with the worker that takes them down.
    follow = {item["service"]: item["node"] for item in recommendation["followUps"]}
    assert best["service"] not in follow
    assert set(follow) | {best["service"]} == {"details", "ratings", "page"}
    # The map can be drawn from the same payload.
    assert {row["service"] for row in body["placement"]} == {"details", "ratings", "page", "reviews"}
    assert best["metric"]["before"] > best["metric"]["after"]
    # The after-change map is a SIMULATED placement of the same services: the changed service gains a replica on a
    # worker that did not host it, every other service is unchanged, and no failure is drawn from invented pods.
    today = {row["service"]: row for row in body["placement"]}
    after = {row["service"]: row for row in best["afterPlacement"]}
    assert set(after) == set(today)
    assert after[best["service"]]["readyReplicas"] == today[best["service"]]["readyReplicas"] + 1
    assert {entry["node"] for entry in after[best["service"]]["nodes"]} > {entry["node"] for entry in today[best["service"]]["nodes"]}
    assert all(after[name]["nodes"] == today[name]["nodes"] for name in today if name != best["service"])
    assert all(option["afterPlacement"] for option in recommendation["alternatives"])


def test_placement_view_counts_only_ready_pods_per_node():
    from app.engine.recommendation_evaluation_validation.placement_view import placement_view

    deployment = {"services": [
        {"name": "b", "pods": [{"node": "n1", "ready": True}, {"node": "n1", "ready": True}, {"node": "n2", "ready": False}]},
        {"name": "a", "pods": []},
    ]}
    assert placement_view(deployment) == [
        {"service": "a", "readyReplicas": 0, "nodes": []},
        {"service": "b", "readyReplicas": 2, "nodes": [{"node": "n1", "readyPods": 2}]},
    ]
    assert placement_view(None) is None
