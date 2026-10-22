"""The ranking inside the live recommendation pipeline: Member 3's real predictor, Phase 1's real evaluator.

The cluster is a fake (no kubeconfig). Scheduling evidence the live topology cannot carry yet is added by the
`evidenced_topology` fixture of test_live_recommendation, exactly as for the other live tests.
"""

import pytest

from app.engine.recommendation_evaluation_validation import live_pipeline
from app.engine.recommendation_evaluation_validation.live_pipeline import build_live_recommendation
from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)
from tests.recommendation_evaluation_validation.test_live_recommendation import (  # noqa: F401  (fixtures)
    LIVE,
    PARAMS,
    concentrated_db,
    evidenced_topology,
    freeze_checkout,
)
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod


def two_concentrated_services() -> FakeClusterSource:
    """db and cart both sit entirely on w1; web is already spread. Neither fix helps without the other."""
    return FakeClusterSource(
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod(f"db-{i}", "w1", app="db") for i in range(3)]
                      + [pod(f"cart-{i}", "w1", app="cart") for i in range(2)]
                      + [pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"shop": [deployment("db", 3), deployment("cart", 2), deployment("web", 2)]},
    )


def test_the_ranked_candidate_is_scored_by_the_formula_and_selected(client, monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())

    report = client.get(LIVE, params=PARAMS).json()

    (candidate,) = report["recommendations"]
    # All risk removed at the assessed node and across the cluster (benefit 1), full evidence (confidence 1),
    # two of the three ready replicas get a new target node: 1 * 1 * (1 - 0.3 * 2/3) * (1 - 0.3 * 0) = 0.8.
    assert candidate["score"] == pytest.approx(0.8)
    assert candidate["riskReduction"] == pytest.approx(100.0)
    assert report["selectedRecommendationId"] == candidate["id"]


def test_the_evidence_explains_the_ranking_and_labels_the_weights_as_experimental(monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())

    live = build_live_recommendation("checkout", "w1", "shop")

    ranking = live.evidence["ranking"]
    assert ranking["weights"] == {"targetShare": 0.7, "disruptionDiscount": 0.3, "resourceDiscount": 0.3,
                                  "status": "experimental defaults; not optimized or proven"}
    assert ranking["basis"] == "single-workflow" and ranking["formulaVersion"]
    assert "evidence coverage" in live.evidence["confidenceMeaning"].lower()
    assert "not the accuracy" in live.evidence["confidenceMeaning"].lower()
    (entry,) = ranking["candidates"].values()
    assert entry["rank"] == 1 and entry["excluded"] is None
    assert entry["components"]["placementChurn"] == pytest.approx(2 / 3)
    assert entry["components"]["survivabilityBefore"] == pytest.approx(0.0)
    assert entry["components"]["survivabilityAfter"] == pytest.approx(100.0)
    assert "rollout" in ranking["placementChurnMeaning"] and "not" in ranking["placementChurnMeaning"]
    assert ranking["selectedCandidate"] == entry["id"]


def test_candidates_that_are_not_verified_feasible_stay_visible_unranked_with_their_reason(monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())   # no scheduling evidence: live discovery cannot verify feasibility

    live = build_live_recommendation("checkout", "w1", "shop")

    (candidate,) = live.report.recommendations
    assert candidate.feasible is False and candidate.score == 0.0
    assert live.report.selected_recommendation_id is None
    (entry,) = live.evidence["ranking"]["candidates"].values()
    assert entry["rank"] is None and entry["excluded"] == "INSUFFICIENT_EVIDENCE"
    assert live.evidence["ranking"]["selectedCandidate"] is None


def test_a_single_replica_candidate_is_listed_unranked_as_infeasible(monkeypatch, use_cluster, evidenced_topology):
    from tests.support.fake_cluster import two_node_shop

    freeze_checkout(monkeypatch)
    use_cluster(two_node_shop())

    live = build_live_recommendation("checkout", None, "shop")

    entries = live.evidence["ranking"]["candidates"]
    assert entries and all(e["rank"] is None for e in entries.values())
    assert any(e["excluded"] == "INFEASIBLE" for e in entries.values())
    assert live.report.selected_recommendation_id is None


def test_a_fix_that_only_works_together_with_another_is_not_ranked_on_its_own(monkeypatch, use_cluster, evidenced_topology):
    # Spreading db alone, or cart alone, still leaves the other one dead on w1, so the workflow still fails.
    # Combined evaluation is outside this phase: each candidate is scored by itself and is shown unranked.
    freeze_checkout(monkeypatch, mandatory=("web", "db", "cart"))
    use_cluster(two_concentrated_services())

    live = build_live_recommendation("checkout", "w1", "shop")

    entries = live.evidence["ranking"]["candidates"]
    assert len(entries) == 2
    assert all(e["rank"] is None and e["excluded"] == "NO_PREDICTED_IMPROVEMENT" for e in entries.values())
    assert live.report.selected_recommendation_id is None
    assert all(c.feasible and c.risk_reduction == 0 and c.score == 0.0 for c in live.report.recommendations)


def test_a_failed_member_3_prediction_on_the_simulated_graph_is_a_visible_exclusion_not_a_crash(
        monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    real, calls = live_pipeline.predict_failure_impact, []

    def flaky(workflow, deployment, failed_node):
        calls.append(failed_node)
        if len(calls) > 1:   # the first call is the baseline; the next ones are on simulated deployments
            raise ValueError("simulated prediction failure")
        return real(workflow, deployment, failed_node)

    monkeypatch.setattr(live_pipeline, "predict_failure_impact", flaky)

    live = build_live_recommendation("checkout", "w1", "shop")

    (entry,) = live.evidence["ranking"]["candidates"].values()
    assert entry["rank"] is None and entry["excluded"] == "MISSING_IMPACT_EVIDENCE"
    assert live.report.selected_recommendation_id is None


def test_ranking_never_applies_anything_to_the_cluster(client, monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    cluster = concentrated_db()
    use_cluster(cluster)

    client.get(LIVE, params=PARAMS)

    assert all(call.startswith(("nodes", "pods", "raw:")) or call == "workload-namespaces" for call in cluster.calls)


def test_the_selected_candidate_is_still_only_a_recommendation_that_needs_a_developer(client, monkeypatch, use_cluster,
                                                                                       evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    rec_id = client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"]

    requested = client.post(f"/api/recommendations/{rec_id}/request-approval", params=PARAMS,
                            json={"recommendationId": rec_id, "proposalVersion": "v1"})

    assert requested.status_code == 200 and requested.json()["status"] == "PENDING_APPROVAL"
    assert requested.json()["evaluationSnapshot"]["score"] == pytest.approx(0.8)
    provenance = requested.json()["evaluationSnapshot"]["provenance"]
    assert provenance["ranking"]["weights"]["status"].startswith("experimental")


# --- the approval gate on top of the real pipeline (nothing is faked between the request and the decision) -----

def spread_db() -> FakeClusterSource:
    """The same services as concentrated_db, but db is now already spread: there is nothing left to recommend."""
    return FakeClusterSource(
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod("db-1", "w1", app="db"), pod("db-2", "w2", app="db"), pod("db-3", "w3", app="db"),
                       pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"shop": [deployment("db", 3), deployment("web", 2)]},
    )


def request_live(client, rec_id):
    return client.post(f"/api/recommendations/{rec_id}/request-approval", params=PARAMS,
                       json={"recommendationId": rec_id, "proposalVersion": "v1"})


def test_approving_re_evaluates_on_the_server_from_the_stored_provenance_not_from_anything_the_client_sends(
        client, monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    rec_id = client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"]
    request_live(client, rec_id)

    # No workflow, namespace or failed node in this request: the server knows them from the stored provenance.
    response = client.post(f"/api/recommendations/{rec_id}/approve", json={"expectedProposalVersion": "v1", "note": "ok"})

    assert response.status_code == 200 and response.json()["status"] == "APPROVED"
    check = response.json()["evaluationSnapshot"]["approvalCheck"]
    assert check["rank"] == 1 and check["isSelected"] is True and check["score"] == pytest.approx(0.8)


def test_a_pending_request_the_cluster_has_overtaken_cannot_be_approved(client, monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    rec_id = client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"]
    request_live(client, rec_id)
    use_cluster(spread_db())      # the replicas were spread by hand in the meantime: the candidate is gone

    response = client.post(f"/api/recommendations/{rec_id}/approve", json={"expectedProposalVersion": "v1", "note": "ok"})

    assert response.status_code == 422 and "current evaluation" in response.json()["detail"]
    assert client.get(f"/api/recommendations/{rec_id}/approval").json()["status"] == "PENDING_APPROVAL"


def test_a_candidate_without_verified_feasibility_cannot_even_be_requested_on_the_real_pipeline(
        client, monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())      # no scheduling evidence: the live topology cannot verify feasibility
    (candidate,) = client.get(LIVE, params=PARAMS).json()["recommendations"]

    response = request_live(client, candidate["id"])

    assert candidate["feasible"] is False and response.status_code == 422


def test_a_feasible_candidate_with_no_predicted_gain_is_shown_but_cannot_be_requested(
        client, monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch, mandatory=("web", "db", "cart"))
    use_cluster(two_concentrated_services())
    report = client.get(LIVE, params=PARAMS).json()
    db = next(c for c in report["recommendations"] if c["service"] == "db")

    response = request_live(client, db["id"])

    assert db["feasible"] is True and db["score"] == 0.0 and report["selectedRecommendationId"] is None
    assert response.status_code == 422 and "not ranked" in response.json()["detail"]
