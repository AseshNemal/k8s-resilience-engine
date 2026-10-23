"""The research approval routes: a named person can approve a declared recommendation for research, and the production routes still refuse it."""

import json

import pytest

from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)
from tests.recommendation_evaluation_validation.test_declared_api import nothing_frozen  # noqa: F401  (fixture)
from tests.recommendation_evaluation_validation.test_independent_inputs import (  # noqa: F401  (fixtures and helpers)
    concentrated_db,
    declared,
)
from tests.recommendation_evaluation_validation.test_research_approval import ACK

PARAMS = {"workflow_id": "checkout", "namespace": "shop"}
PROD = "/api/recommendations"
RESEARCH = "/api/recommendations/declared"


def selected_id(client):
    return client.get(f"{RESEARCH}", params=PARAMS).json()["selectedRecommendationId"]


def request_body(candidate, **overrides):
    return {"recommendationId": candidate, "proposalVersion": "v1", "acknowledgedBy": "asesh", "acknowledgement": ACK, **overrides}


def decision(**overrides):
    return {"note": "approve for research", "expectedProposalVersion": "v1", "decidedBy": "asesh", **overrides}


def test_the_declared_detail_offers_the_research_door_without_reopening_the_production_controls(client, use_cluster, declared, nothing_frozen):
    use_cluster(concentrated_db())

    detail = client.get(f"{RESEARCH}/detail", params=PARAMS).json()

    feasible = [c for c in detail["candidates"] if c["feasible"]]
    assert feasible and all(c["approval"]["ready"] is False for c in detail["candidates"])
    offered = [c["researchApproval"] for c in feasible if c["researchApproval"]["available"]]
    assert offered and "disposable test cluster only" in offered[0]["scope"]
    assert offered[0]["requestWith"].startswith("POST /api/recommendations/declared/")
    blocked = [c["researchApproval"] for c in detail["candidates"] if not c["feasible"]]
    assert all(b["available"] is False and "cannot be approved in any tier" in b["reason"] for b in blocked)


def test_a_named_person_can_request_and_approve_a_declared_recommendation_for_research(client, use_cluster, declared, nothing_frozen, sqlite_url):
    use_cluster(concentrated_db())
    candidate = selected_id(client)

    requested = client.post(f"{RESEARCH}/{candidate}/request-approval", params=PARAMS, json=request_body(candidate))
    approved = client.post(f"{RESEARCH}/{candidate}/approve", params=PARAMS, json=decision())

    assert requested.status_code == 200 and requested.headers["X-Evidence-Provenance"] == "research-declared"
    assert requested.json()["status"] == "PENDING_APPROVAL" and requested.json()["requestedBy"] == "asesh"
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED" and approved.json()["decidedBy"] == "asesh"
    shown = client.get(f"{RESEARCH}/{candidate}/approval")
    assert shown.status_code == 200 and shown.headers["X-Evidence-Provenance"] == "research-declared"
    provenance = shown.json()["evaluationSnapshot"]["provenance"]
    assert provenance["source"] == "declared-workflow" and provenance["researchAcknowledgement"]["by"] == "asesh"


def test_the_production_routes_still_refuse_what_the_research_tier_made(client, use_cluster, declared, nothing_frozen, sqlite_url):
    use_cluster(concentrated_db())
    candidate = selected_id(client)
    client.post(f"{RESEARCH}/{candidate}/request-approval", params=PARAMS, json=request_body(candidate))

    production = client.post(f"{PROD}/{candidate}/approve", params=PARAMS,
                             json={"note": "n", "expectedProposalVersion": "v1"})
    shown = client.get(f"{PROD}/{candidate}/approval")

    # the record is still PENDING, so it is the provenance check that refuses it, before any evaluation is attempted
    assert production.status_code == 409 and production.json()["detail"]["code"] == "APPROVAL_PROVENANCE_UNVERIFIED"
    assert shown.headers["X-Evidence-Provenance"] == "unverified"
    assert client.get(f"{RESEARCH}/{candidate}/approval").json()["status"] == "PENDING_APPROVAL"


@pytest.mark.parametrize("override, status", [({"acknowledgedBy": ""}, 422), ({"acknowledgedBy": "developer"}, 422),
                                              ({"acknowledgement": "ok"}, 422)])
def test_a_request_without_a_named_person_or_a_real_acknowledgement_is_refused_and_leaves_no_record(
        client, use_cluster, declared, nothing_frozen, sqlite_url, override, status):
    use_cluster(concentrated_db())
    candidate = selected_id(client)

    refused = client.post(f"{RESEARCH}/{candidate}/request-approval", params=PARAMS, json=request_body(candidate, **override))

    assert refused.status_code == status and client.get(f"{RESEARCH}/{candidate}/approval").status_code == 404


def test_an_anonymous_decision_is_refused_and_the_request_stays_pending(client, use_cluster, declared, nothing_frozen, sqlite_url):
    use_cluster(concentrated_db())
    candidate = selected_id(client)
    client.post(f"{RESEARCH}/{candidate}/request-approval", params=PARAMS, json=request_body(candidate))

    refused = client.post(f"{RESEARCH}/{candidate}/approve", params=PARAMS, json=decision(decidedBy="developer"))

    assert refused.status_code == 422
    assert client.get(f"{RESEARCH}/{candidate}/approval").json()["status"] == "PENDING_APPROVAL"


def test_a_declaration_changed_between_the_request_and_the_decision_makes_it_stale(client, use_cluster, declared, nothing_frozen, sqlite_url):
    use_cluster(concentrated_db())
    candidate = selected_id(client)
    client.post(f"{RESEARCH}/{candidate}/request-approval", params=PARAMS, json=request_body(candidate))
    declared.write_text(json.dumps({**json.loads(declared.read_text()), "criticality": 0.3}))

    stale = client.post(f"{RESEARCH}/{candidate}/approve", params=PARAMS, json=decision())

    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "APPROVAL_PROPOSAL_STALE"
    assert client.get(f"{RESEARCH}/{candidate}/approval").json()["status"] == "PENDING_APPROVAL"


def test_a_request_can_be_rejected_by_a_named_person(client, use_cluster, declared, nothing_frozen, sqlite_url):
    use_cluster(concentrated_db())
    candidate = selected_id(client)
    client.post(f"{RESEARCH}/{candidate}/request-approval", params=PARAMS, json=request_body(candidate))

    rejected = client.post(f"{RESEARCH}/{candidate}/reject", params=PARAMS, json=decision(note="not now"))

    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED" and rejected.json()["decidedBy"] == "asesh"


def test_a_workflow_that_is_not_declared_cannot_be_requested_through_the_research_door(client, use_cluster, declared, nothing_frozen, sqlite_url):
    use_cluster(concentrated_db())

    refused = client.post(f"{RESEARCH}/some-candidate/request-approval", params={"workflow_id": "unknown", "namespace": "shop"},
                          json=request_body("some-candidate"))

    assert refused.status_code == 404 and refused.json()["detail"]["code"] == "DECLARED_WORKFLOW_UNAVAILABLE"


def test_the_research_reject_route_cannot_touch_a_production_record_and_needs_a_named_person(client, monkeypatch, use_cluster, declared, sqlite_url):
    from tests.recommendation_evaluation_validation.test_independent_inputs import freeze_checkout

    use_cluster(concentrated_db())
    freeze_checkout(monkeypatch)
    candidate = client.get(f"{PROD}/live", params=PARAMS).json()["selectedRecommendationId"]
    client.post(f"{PROD}/{candidate}/request-approval", params=PARAMS, json={"recommendationId": candidate, "proposalVersion": "v1"})

    wrong_tier = client.post(f"{RESEARCH}/{candidate}/reject", params=PARAMS, json=decision())
    anonymous = client.post(f"{RESEARCH}/{candidate}/reject", params=PARAMS, json=decision(decidedBy="developer"))

    assert wrong_tier.status_code == 409 and wrong_tier.json()["detail"]["code"] == "APPROVAL_RESEARCH_PROVENANCE_MISSING"
    assert anonymous.status_code == 422
    assert client.get(f"{PROD}/{candidate}/approval").json()["status"] == "PENDING_APPROVAL"      # still pending, still the production gate's


def test_a_research_request_over_a_production_record_is_refused_over_http(client, monkeypatch, use_cluster, declared, sqlite_url):
    from tests.recommendation_evaluation_validation.test_independent_inputs import freeze_checkout

    use_cluster(concentrated_db())
    freeze_checkout(monkeypatch)
    candidate = client.get(f"{PROD}/live", params=PARAMS).json()["selectedRecommendationId"]
    client.post(f"{PROD}/{candidate}/request-approval", params=PARAMS, json={"recommendationId": candidate, "proposalVersion": "v1"})

    refused = client.post(f"{RESEARCH}/{candidate}/request-approval", params=PARAMS, json=request_body(candidate))

    assert refused.status_code == 422 and "not a research-tier one" in refused.json()["detail"]
    assert client.get(f"{PROD}/{candidate}/approval").headers["X-Evidence-Provenance"] == "verified"
