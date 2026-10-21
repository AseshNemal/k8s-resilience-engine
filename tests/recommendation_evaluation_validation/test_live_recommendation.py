"""Recommendations computed from the connected cluster and a frozen workflow, never from a fixture."""

from types import SimpleNamespace

import pytest

from app.engine.recommendation_evaluation_validation.approval import ApprovalRepository
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph
from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod, two_node_shop

LIVE = "/api/recommendations/live"
PARAMS = {"workflow_id": "checkout", "namespace": "shop"}


def concentrated_db() -> FakeClusterSource:
    """db has 3 replicas, all on w1; web is already spread over w2 and w3."""
    return FakeClusterSource(
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod(f"db-{i}", "w1", app="db") for i in range(3)]
                      + [pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"shop": [deployment("db", 3), deployment("web", 2)]},
    )


def freeze_checkout(monkeypatch, mandatory=("web", "db")):
    graph = WorkflowGraph(workflow_id="checkout", criticality=0.9, mandatory_services=list(mandatory),
                          edges=[WorkflowEdge(source="web", target="db")])
    monkeypatch.setattr(
        workflow_service, "get_frozen_workflow_graph",
        lambda workflow_id: SimpleNamespace(graph=graph, freeze_id="f1", assessment_id="a1"),
    )


def test_a_concentrated_service_gets_a_feasible_recommendation_with_a_predicted_gain(client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())

    response = client.get(LIVE, params=PARAMS)

    assert response.status_code == 200
    report = response.json()
    (candidate,) = report["recommendations"]
    assert (candidate["type"], candidate["service"], candidate["feasible"]) == ("TOPOLOGY_SPREAD", "db", True)
    # Losing w1 takes all three db replicas, so the workflow fails; spread over three workers it survives.
    assert candidate["riskReduction"] == pytest.approx(100.0)
    assert candidate["predictedBlastRadius"] == pytest.approx(0.0)
    assert candidate["confidence"] == pytest.approx(1.0)
    assert candidate["score"] == pytest.approx(1.0)
    assert report["selectedRecommendationId"] == candidate["id"]
    assert report["workflowId"] == "checkout"


def test_the_response_says_which_evidence_it_came_from(client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())

    response = client.get(LIVE, params=PARAMS)

    assert response.headers["X-Evidence-Source"] == "live-discovery"
    assert response.headers["X-Topology-Captured-At"].endswith("Z")


def test_without_a_named_failed_node_the_worst_one_is_used(client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())

    client.get(LIVE, params=PARAMS)
    from app.engine.recommendation_evaluation_validation.live_pipeline import build_live_recommendation

    live = build_live_recommendation("checkout", None, "shop")

    assert live.evidence["failedNode"] == "w1"
    assert live.evidence["failedNodeChosen"] == "highest blast radius"
    assert live.evidence["blastRadiusBefore"] == pytest.approx(100.0)


def test_a_named_failed_node_is_used_as_given(client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())

    # w2 holds only a web replica; losing it does not fail the workflow, so there is nothing to gain.
    response = client.get(LIVE, params={**PARAMS, "failed_node": "w2"})

    assert response.status_code == 200
    assert response.json()["recommendations"] == []
    assert response.json()["selectedRecommendationId"] is None


def test_an_unknown_failed_node_is_a_client_error(client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())

    response = client.get(LIVE, params={**PARAMS, "failed_node": "nope"})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_RECOMMENDATION_REQUEST"


def test_a_well_spread_cluster_needs_no_recommendation(client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    spread = FakeClusterSource(
        nodes=[node("w1"), node("w2")],
        pods={"shop": [pod("db-1", "w1", app="db"), pod("db-2", "w2", app="db"),
                       pod("web-1", "w1", app="web"), pod("web-2", "w2", app="web")]},
        deployments={"shop": [deployment("db", 2), deployment("web", 2)]},
    )
    use_cluster(spread)

    response = client.get(LIVE, params=PARAMS)

    # Losing either worker leaves a replica of every service, so there is nothing to improve.
    assert response.status_code == 200
    assert response.json()["recommendations"] == []
    assert response.json()["selectedRecommendationId"] is None


def test_a_single_replica_service_gets_an_infeasible_candidate_not_silence(client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    use_cluster(two_node_shop())

    response = client.get(LIVE, params=PARAMS)

    # db has one replica on worker-1: spreading cannot help, and the report says so instead of omitting it.
    (candidate,) = [c for c in response.json()["recommendations"] if c["service"] == "db"]
    assert candidate["feasible"] is False and candidate["riskReduction"] == 0
    assert response.json()["selectedRecommendationId"] is None


def test_a_service_that_cannot_be_spread_is_reported_infeasible_with_no_gain(client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    one_node = FakeClusterSource(
        nodes=[node("w1")],
        pods={"shop": [pod(f"db-{i}", "w1", app="db") for i in range(2)] + [pod("web-1", "w1", app="web")]},
        deployments={"shop": [deployment("db", 2), deployment("web", 1)]},
    )
    use_cluster(one_node)

    response = client.get(LIVE, params=PARAMS)

    candidates = response.json()["recommendations"]
    assert candidates and all(c["feasible"] is False and c["riskReduction"] == 0 for c in candidates)
    assert response.json()["selectedRecommendationId"] is None


def test_the_real_topology_is_never_changed_by_the_simulation(monkeypatch, use_cluster):
    from app.engine.failure_impact_prediction.runtime_provider import get_runtime_component_inputs
    from app.engine.failure_impact_prediction.service import predict_failure_impact
    from app.engine.recommendation_evaluation_validation.candidate_generation import (
        CandidateContextBuilder,
        generate_candidates,
    )
    from app.engine.recommendation_evaluation_validation.service import evaluate_candidate

    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    inputs = get_runtime_component_inputs("checkout", "shop")
    impact = predict_failure_impact(inputs.workflow, inputs.deployment, "w1")
    before = inputs.deployment.model_dump()

    for spec in generate_candidates(CandidateContextBuilder.build(inputs.deployment, impact)):
        evaluate_candidate(inputs.deployment, spec)

    assert inputs.deployment.model_dump() == before


def test_an_incomplete_topology_is_refused_not_guessed_at(client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    cluster = concentrated_db()
    cluster.pods["shop"].append(pod("db-9", None, app="db", ready=False, phase="Pending"))
    use_cluster(cluster)

    response = client.get(LIVE, params=PARAMS)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TOPOLOGY_INCOMPLETE"


def test_an_unreadable_cluster_is_a_coded_error(client, monkeypatch, use_cluster):
    from app.integrations.kubernetes.client import NotConnectedError

    freeze_checkout(monkeypatch)
    use_cluster(FakeClusterSource(error=NotConnectedError("x")))

    response = client.get(LIVE, params=PARAMS)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CLUSTER_NOT_CONNECTED"


def test_a_missing_frozen_workflow_is_a_404_with_a_stable_code(client, monkeypatch, use_cluster):
    def missing(workflow_id):
        raise LookupError("no frozen graph")

    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", missing)
    use_cluster(concentrated_db())

    response = client.get(LIVE, params=PARAMS)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "WORKFLOW_EVIDENCE_UNAVAILABLE"


def test_without_a_workflow_nothing_is_computed(client):
    response = client.get(LIVE, params={"namespace": "shop"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RECOMMENDATION_EVIDENCE_UNAVAILABLE"


def test_an_approval_can_be_requested_for_a_live_recommendation_and_is_marked_verified(
        client, monkeypatch, use_cluster, sqlite_url):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    rec_id = client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"]

    requested = client.post(
        f"/api/recommendations/{rec_id}/request-approval", params=PARAMS,
        json={"recommendationId": rec_id, "proposalVersion": "v1"},
    )

    assert requested.status_code == 200
    assert requested.json()["status"] == "PENDING_APPROVAL"
    assert requested.headers["X-Evidence-Provenance"] == "verified"
    provenance = requested.json()["evaluationSnapshot"]["provenance"]
    assert provenance["source"] == "live-discovery"
    assert provenance["failedNode"] == "w1"
    assert provenance["isPrediction"] is True


def test_nothing_is_applied_to_the_cluster(client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    cluster = concentrated_db()
    use_cluster(cluster)

    client.get(LIVE, params=PARAMS)

    assert all(call.startswith(("nodes", "pods", "raw:")) or call == "workload-namespaces" for call in cluster.calls)
