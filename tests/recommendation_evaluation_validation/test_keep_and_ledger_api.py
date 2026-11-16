"""The routes behind "Reset now or keep the change" and the open-changes ledger, through the real application."""

from dataclasses import replace

from tests.recommendation_evaluation_validation.test_application_api import (  # noqa: F401  (fixtures and helpers)
    PARAMS,
    PROD,
    RESEARCH,
    approved_candidate,
    client,
    cluster,
    connected,
    declared,
    nothing_frozen,
    override_get_db,
    sqlite_url,
)


def apply(client, candidate):
    response = client.post(f"{RESEARCH}/{candidate}/apply", params=PARAMS, json={"appliedBy": "asesh"})
    assert response.status_code == 200
    return response.json()


def test_the_status_carries_a_readable_account_of_the_change(client, connected, approved_candidate):
    body = apply(client, approved_candidate)

    assert body["canKeep"] is True
    row = body["changes"][0]
    assert row["deployment"] == "db" and row["summary"].startswith("db: ")
    assert {f["field"] for f in row["fields"]} == {"replicas", "topology_spread_constraints", "pod_anti_affinity"}


def test_keeping_a_change_frees_the_cluster_for_another_and_leaves_it_resettable(client, connected, approved_candidate):
    apply(client, approved_candidate)

    kept = client.post(f"{RESEARCH}/{approved_candidate}/keep", json={"keptBy": "asesh"})

    assert kept.status_code == 200
    body = kept.json()
    assert body["application"]["status"] == "KEPT" and body["canKeep"] is False and body["canReset"] is True
    assert connected.deployments["db"].has_hostname_spread, "keeping writes nothing"
    ledger = client.get(f"{PROD}/applications/outstanding").json()
    assert [row["status"] for row in ledger["changes"]] == ["KEPT"] and ledger["blocking"] == []


def test_keeping_what_is_not_applied_is_refused(client, connected, approved_candidate):
    response = client.post(f"{RESEARCH}/{approved_candidate}/keep", json={"keptBy": "asesh"})

    assert response.status_code == 409 and response.json()["detail"]["code"] == "NOTHING_TO_KEEP"


def test_the_ledger_route_lists_what_is_still_applied_for_either_tier(client, connected, approved_candidate):
    assert client.get(f"{PROD}/applications/outstanding").json()["changes"] == []

    apply(client, approved_candidate)
    ledger = client.get(f"{PROD}/applications/outstanding").json()

    assert [row["recommendationId"] for row in ledger["blocking"]] == [approved_candidate]
    assert ledger["blocking"][0]["tier"] == "research" and ledger["blocking"][0]["clusterMatch"] == "same"


def test_reset_names_an_outside_change_and_restores_nothing_until_told_to_keep_it(client, connected, approved_candidate):
    apply(client, approved_candidate)
    connected.deployments["db"] = replace(connected.deployments["db"], replicas=9)

    refused = client.post(f"{RESEARCH}/{approved_candidate}/reset", json={"resetBy": "asesh"})

    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "EXTERNAL_CHANGE" and detail["details"]["external"][0]["field"] == "replicas"
    assert connected.deployments["db"].has_hostname_spread and connected.restores == []

    kept = client.post(f"{RESEARCH}/{approved_candidate}/reset", json={"resetBy": "asesh", "external": "keep"})

    assert kept.status_code == 200 and kept.json()["application"]["status"] == "RESET"
    assert connected.deployments["db"].replicas == 9 and not connected.deployments["db"].has_hostname_spread


def test_reset_rejects_an_external_choice_it_does_not_know(client, connected, approved_candidate):
    apply(client, approved_candidate)

    response = client.post(f"{RESEARCH}/{approved_candidate}/reset", json={"resetBy": "asesh", "external": "overwrite"})

    assert response.status_code == 422
