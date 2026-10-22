"""An approval is bound to the exact proposal it was requested for, on the real pipeline.

Nothing is faked between the request and the decision: the fake cluster feeds the live pipeline, which feeds the
approval gate. A request is refused as stale when the plan, the predicted effect or the scheduling constraints
it was made under have changed, even though its candidate still has the same id and is still ranked first.
"""

import pytest

from app.engine.recommendation_evaluation_validation import live_pipeline
from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)
from tests.recommendation_evaluation_validation.test_live_combinations import (
    CART,
    COMBO,
    DB,
    freeze,
    live,
)
from tests.recommendation_evaluation_validation.test_live_ranking import request_live, two_concentrated_services
from tests.recommendation_evaluation_validation.test_live_recommendation import (  # noqa: F401  (fixtures)
    LIVE,
    PARAMS,
    add_scheduling_evidence,
    concentrated_db,
    evidenced_topology,
    freeze_checkout,
)
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod


# --- the plan and its predicted effect

def db_with_replicas(count: int) -> FakeClusterSource:
    return FakeClusterSource(
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod(f"db-{i}", "w1", app="db") for i in range(count)]
                      + [pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"shop": [deployment("db", count), deployment("web", 2)]},
    )


def test_a_pending_request_is_not_approved_after_the_plan_it_describes_has_changed(
        client, monkeypatch, use_cluster, evidenced_topology):
    # The candidate keeps its id and stays ranked first, but db now has four replicas: the plan, the pods that
    # would have to move and the predicted score are not what the developer was shown.
    freeze_checkout(monkeypatch)
    use_cluster(db_with_replicas(3))
    rec_id = client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"]
    request_live(client, rec_id)
    use_cluster(db_with_replicas(4))
    assert client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"] == rec_id    # still eligible

    response = client.post(f"/api/recommendations/{rec_id}/approve", json={"expectedProposalVersion": "v1", "note": "ok"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"
    assert client.get(f"/api/recommendations/{rec_id}/approval").json()["status"] == "PENDING_APPROVAL"


# --- approval: a combination is one proposal, bound to its exact plan ---------------------------------------------------

def test_a_combination_can_be_requested_and_approved_as_one_proposal(client, monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())
    assert client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"] == COMBO

    requested = request_live(client, COMBO)
    approved = client.post(f"/api/recommendations/{COMBO}/approve", json={"expectedProposalVersion": "v1", "note": ""})

    assert requested.status_code == 200 and approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    snapshot = approved.json()["evaluationSnapshot"]
    assert snapshot["provenance"]["combinations"]["entries"][COMBO]["members"] == [CART, DB]
    assert client.get(f"/api/recommendations/{CART}/approval").status_code == 404        # the members were not approved


def test_a_member_on_its_own_is_not_approvable_when_it_improves_nothing(client, monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())

    assert request_live(client, DB).status_code == 422 and request_live(client, CART).status_code == 422


def test_a_combination_is_not_approved_after_the_cluster_changed_under_it(client, monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())
    request_live(client, COMBO)
    drifted = FakeClusterSource(
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod(f"db-{i}", "w1", app="db") for i in range(3)]
                      + [pod(f"cart-{i}", "w1", app="cart") for i in range(3)]       # cart grew to three replicas
                      + [pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"shop": [deployment("db", 3), deployment("cart", 3), deployment("web", 2)]})
    use_cluster(drifted)

    response = client.post(f"/api/recommendations/{COMBO}/approve", json={"expectedProposalVersion": "v1", "note": "ok"})

    assert response.status_code == 409 and response.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"
    assert client.get(f"/api/recommendations/{COMBO}/approval").json()["status"] == "PENDING_APPROVAL"


def test_the_topology_snapshot_is_recorded_for_audit_with_the_evidence_and_the_request(
        client, monkeypatch, use_cluster, evidenced_topology):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())

    evidence = live().evidence
    requested = request_live(client, COMBO)

    assert evidence["topologySnapshotId"].startswith("snap-")
    assert requested.json()["evaluationSnapshot"]["proposal"]["topologySnapshotId"] == evidence["topologySnapshotId"]


# --- the scheduling constraints the verdict was reached under are part of the proposal ------------------------------

@pytest.fixture
def node_selectors(monkeypatch):
    """Like evidenced_topology, but each service's nodeSelector can be changed between two evaluations."""
    selectors: dict[str, dict] = {}
    original = live_pipeline.get_runtime_component_inputs

    def with_evidence(workflow_id, namespace="default"):
        inputs = original(workflow_id, namespace)
        evidence = add_scheduling_evidence(inputs.deployment)
        for worker in inputs.deployment.nodes:
            worker.labels = {**worker.labels, "disk": "ssd"}            # every node matches the selector either way
        for service in inputs.deployment.services:
            service.declared_intent.node_selector = dict(selectors.get(service.name, {}))
        monkeypatch.setattr(live_pipeline, "scheduling_evidence_for", lambda deployment: evidence)
        return inputs

    monkeypatch.setattr(live_pipeline, "get_runtime_component_inputs", with_evidence)
    return selectors


def test_a_new_scheduling_constraint_makes_a_pending_request_stale_even_when_the_plan_is_identical(
        client, monkeypatch, use_cluster, node_selectors):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    rec_id = client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"]
    plan_before = live_pipeline.build_live_recommendation("checkout", "w1", "shop").evidence["feasibility"][rec_id]["placement"]
    request_live(client, rec_id)
    node_selectors["db"] = {"disk": "ssd"}                 # admits every node: the same plan still works
    plan_after = live_pipeline.build_live_recommendation("checkout", "w1", "shop").evidence["feasibility"][rec_id]["placement"]
    assert plan_after == plan_before                                    # the placement did not change at all

    response = client.post(f"/api/recommendations/{rec_id}/approve", json={"expectedProposalVersion": "v1", "note": "ok"})

    assert response.status_code == 409 and response.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"
    assert client.get(f"/api/recommendations/{rec_id}/approval").json()["status"] == "PENDING_APPROVAL"


def test_the_same_constraints_leave_the_request_approvable(client, monkeypatch, use_cluster, node_selectors):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    node_selectors["db"] = {"disk": "ssd"}
    rec_id = client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"]
    request_live(client, rec_id)

    response = client.post(f"/api/recommendations/{rec_id}/approve", json={"expectedProposalVersion": "v1", "note": "ok"})

    assert response.status_code == 200


def test_a_pair_that_could_not_be_verified_jointly_cannot_even_be_requested(client, monkeypatch, use_cluster, evidenced_topology):
    from tests.recommendation_evaluation_validation.test_live_combinations import scarce_room

    freeze(monkeypatch)
    use_cluster(scarce_room())
    entry = live().evidence["combinations"]["entries"][COMBO]
    assert entry["status"] == "INSUFFICIENT_EVIDENCE"                    # inconclusive, not a proof of anything

    response = request_live(client, COMBO)

    assert response.status_code == 422
    assert client.get(f"/api/recommendations/{COMBO}/approval").status_code == 404


def test_a_new_constraint_on_either_member_of_a_pair_makes_the_pair_stale(client, monkeypatch, use_cluster, node_selectors):
    freeze(monkeypatch)
    use_cluster(two_concentrated_services())
    request_live(client, COMBO)
    node_selectors["cart"] = {"disk": "ssd"}            # only cart changed; db, the plan and the ranking did not

    response = client.post(f"/api/recommendations/{COMBO}/approve", json={"expectedProposalVersion": "v1", "note": "ok"})

    assert response.status_code == 409 and response.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"
