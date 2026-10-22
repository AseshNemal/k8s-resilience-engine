"""An approval is bound to the exact YAML the server generated, not to text a client typed."""

import dataclasses

import pytest

from app.engine.recommendation_evaluation_validation.approval import (
    LIVE_EVIDENCE_SOURCE,
    ApprovalRepository,
    ApprovalService,
    StaleProposalError,
    proposal_fingerprint,
)
from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)
from tests.recommendation_evaluation_validation.test_live_ranking import request_live
from tests.recommendation_evaluation_validation.test_live_recommendation import (  # noqa: F401  (fixtures)
    LIVE,
    PARAMS,
    concentrated_db,
    evidenced_topology,
    freeze_checkout,
)
from tests.support.member4_harness import bookinfo_scenario

DETAIL = "/api/recommendations/live/detail"
REVIEWS = "rec-productpage-view-reviews-topology-spread"


def selected_id(client):
    return client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"]


def test_the_requested_yaml_is_the_one_the_server_generated(client, monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    rec_id = selected_id(client)

    response = request_live(client, rec_id)

    assert response.status_code == 200
    body = response.json()
    assert "topologySpreadConstraints" in body["yamlSnapshot"] and "NOT APPLIED" in body["yamlSnapshot"]
    recorded = body["evaluationSnapshot"]["proposal"]["yaml"]
    assert recorded["origin"] == "server-generated" and recorded["verifiedAgainstRecommendation"] is True


def test_a_client_cannot_substitute_other_yaml(client, monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    rec_id = selected_id(client)

    response = client.post(f"/api/recommendations/{rec_id}/request-approval", params=PARAMS, json={
        "recommendationId": rec_id, "proposalVersion": "v1",
        "yamlSnapshot": "kind: Deployment\nspec:\n  replicas: 0\n"})

    assert response.status_code == 422
    assert "not the YAML the server generates" in response.json()["detail"]
    assert client.get(f"/api/recommendations/{rec_id}/approval").status_code == 404


def test_the_identical_text_is_accepted_and_approval_verifies_it(client, monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    rec_id = selected_id(client)
    text = client.get(DETAIL, params=PARAMS).json()["yaml"]["text"]

    requested = client.post(f"/api/recommendations/{rec_id}/request-approval", params=PARAMS, json={
        "recommendationId": rec_id, "proposalVersion": "v1", "yamlSnapshot": text})
    approved = client.post(f"/api/recommendations/{rec_id}/approve", json={"expectedProposalVersion": "v1", "note": "ok"})

    assert requested.status_code == 200 and approved.status_code == 200
    check = approved.json()["evaluationSnapshot"]["approvalCheck"]
    assert check["yamlVerifiedAgainstRecommendation"] is True and check["appliesNothing"] is True


class _Memory(ApprovalRepository):
    """One approval in memory: the gate under test without a database."""

    def __init__(self):  # noqa: D107
        self.item = None

    def get_by_recommendation_id(self, recommendation_id):
        return self.item

    def save(self, approval):
        approval.id, self.item = 1, approval
        return approval

    def update(self, approval):
        self.item = approval
        return approval


def as_live(live):
    return dataclasses.replace(live, evidence={**live.evidence, "source": LIVE_EVIDENCE_SOURCE})


def test_a_deployment_that_changed_under_a_pending_request_makes_it_stale():
    scenario = bookinfo_scenario("concentrated")
    live = as_live(scenario.evaluate())
    service = ApprovalService(_Memory())
    service.request_approval(REVIEWS, "v1", live.report, requested_by="t", evidence=live.evidence,
                             proposal_yaml=build_yaml_proposal(live, REVIEWS).yaml)

    # The Deployment is renamed: the placement plan is the same, the YAML that would name it is not.
    changed = as_live(scenario.evaluate())
    for svc in changed.details["currentDeployment"]["services"]:
        if svc["name"] == "reviews":
            svc["deploymentNames"] = ["reviews-v2"]

    assert proposal_fingerprint(changed, REVIEWS) == proposal_fingerprint(live, REVIEWS)
    with pytest.raises(StaleProposalError, match="YAML the server generates"):
        service.approve(REVIEWS, "v1", "ok", evaluate=lambda approval: changed)


def test_a_client_supplied_yaml_is_still_never_reported_as_verified():
    live = as_live(bookinfo_scenario("concentrated").evaluate())
    service = ApprovalService(_Memory())

    approval = service.request_approval(REVIEWS, "v1", live.report, yaml_snapshot="kind: Deployment\n",
                                        requested_by="t", evidence=live.evidence)

    recorded = approval.evaluation_snapshot["proposal"]["yaml"]
    assert recorded["origin"] == "client-supplied" and recorded["verifiedAgainstRecommendation"] is False
