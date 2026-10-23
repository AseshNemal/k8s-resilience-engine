"""The Apply and Reset routes: the research door applies a research approval, the production door does not, and nothing applies by being read."""

import pytest

from app.api.recommendations import get_cluster_applier
from app.main import app
from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)
from tests.recommendation_evaluation_validation.test_application import cluster, db_deployment, other_deployment  # noqa: F401
from tests.recommendation_evaluation_validation.test_declared_api import nothing_frozen  # noqa: F401  (fixture)
from tests.recommendation_evaluation_validation.test_independent_inputs import (  # noqa: F401  (fixtures and helpers)
    concentrated_db,
    declared,
)
from tests.recommendation_evaluation_validation.test_research_api import PARAMS, decision, request_body, selected_id

PROD, RESEARCH = "/api/recommendations", "/api/recommendations/declared"


@pytest.fixture
def connected(cluster, monkeypatch):
    monkeypatch.setenv("RESILIENCE_ALLOW_CLUSTER_WRITES", "1")           # what the extension's compose file does
    app.dependency_overrides[get_cluster_applier] = lambda: (lambda: cluster)
    yield cluster
    app.dependency_overrides.pop(get_cluster_applier, None)


@pytest.fixture
def approved_candidate(client, use_cluster, declared, nothing_frozen, sqlite_url):
    use_cluster(concentrated_db())
    candidate = selected_id(client)
    assert client.post(f"{RESEARCH}/{candidate}/request-approval", params=PARAMS, json=request_body(candidate)).status_code == 200
    assert client.post(f"{RESEARCH}/{candidate}/approve", params=PARAMS, json=decision()).status_code == 200
    return candidate


def test_nothing_is_applied_by_approving_or_by_asking(client, connected, approved_candidate):
    status = client.get(f"{RESEARCH}/{approved_candidate}/application").json()

    assert status["canApply"] is True and status["application"] is None and connected.patches == []


def test_the_research_door_applies_and_resets_an_approved_change(client, connected, approved_candidate):
    applied = client.post(f"{RESEARCH}/{approved_candidate}/apply", params=PARAMS, json={"appliedBy": "asesh"})

    assert applied.status_code == 200
    body = applied.json()
    assert body["application"]["status"] == "APPLIED" and body["application"]["deployments"] == ["db"]
    assert body["canReset"] is True and body["canApply"] is False and body["rolloutComplete"] is True
    assert connected.deployments["db"].has_hostname_spread

    reset = client.post(f"{RESEARCH}/{approved_candidate}/reset", json={"resetBy": "asesh"})

    assert reset.status_code == 200 and reset.json()["application"]["status"] == "RESET" and reset.json()["canApply"] is True
    assert not connected.deployments["db"].has_hostname_spread


def test_the_production_door_does_not_apply_a_research_approval(client, connected, approved_candidate):
    refused = client.post(f"{PROD}/{approved_candidate}/apply", params=PARAMS, json={"appliedBy": "asesh"})

    assert refused.status_code == 409 and refused.json()["detail"]["code"] in ("PROVENANCE_NOT_ACCEPTED", "NOT_APPROVED")
    assert connected.patches == []


def test_a_change_to_the_declaration_after_approval_blocks_the_apply(client, connected, approved_candidate, tmp_path, monkeypatch):
    import json

    from app.engine.recommendation_evaluation_validation.declared_workflow import ENV_NAME
    from tests.recommendation_evaluation_validation.test_independent_inputs import DECLARATION

    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps({**DECLARATION, "note": "edited after approval"}))
    monkeypatch.setenv(ENV_NAME, str(edited))

    refused = client.post(f"{RESEARCH}/{approved_candidate}/apply", params=PARAMS, json={"appliedBy": "asesh"})

    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "APPROVAL_STALE" and connected.patches == []


def test_an_applier_must_be_named_and_a_missing_cluster_is_an_explicit_error(client, connected, approved_candidate):
    nameless = client.post(f"{RESEARCH}/{approved_candidate}/apply", params=PARAMS, json={"appliedBy": ""})
    assert nameless.status_code == 422 and nameless.json()["detail"]["code"] == "APPLIER_REQUIRED"

    app.dependency_overrides[get_cluster_applier] = lambda: (lambda: (_ for _ in ()).throw(
        __import__("app.integrations.kubernetes.client", fromlist=["NotConnectedError"]).NotConnectedError("none")))
    missing = client.post(f"{RESEARCH}/{approved_candidate}/apply", params=PARAMS, json={"appliedBy": "asesh"})

    assert missing.status_code == 503 and missing.json()["detail"]["code"] == "CLUSTER_NOT_CONNECTED"


def test_the_real_applier_is_built_only_for_the_connected_cluster_and_refuses_a_missing_file(monkeypatch, tmp_path):
    from app.integrations.kubernetes.client import NotConnectedError

    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "none"))

    with pytest.raises(NotConnectedError):
        get_cluster_applier()()


def test_a_backend_that_was_not_armed_refuses_every_write_and_the_page_is_told(client, connected, approved_candidate, monkeypatch):
    monkeypatch.delenv("RESILIENCE_ALLOW_CLUSTER_WRITES")

    refused = client.post(f"{RESEARCH}/{approved_candidate}/apply", params=PARAMS, json={"appliedBy": "asesh"})
    reset = client.post(f"{RESEARCH}/{approved_candidate}/reset", json={"resetBy": "asesh"})
    status = client.get(f"{RESEARCH}/{approved_candidate}/application").json()

    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "CLUSTER_WRITES_NOT_ENABLED"
    assert reset.status_code == 403
    assert status["canApply"] is False and status["writesEnabled"] is False and connected.patches == []


def test_a_request_cannot_arm_the_backend(client, connected, approved_candidate, monkeypatch):
    monkeypatch.delenv("RESILIENCE_ALLOW_CLUSTER_WRITES")

    refused = client.post(f"{RESEARCH}/{approved_candidate}/apply?RESILIENCE_ALLOW_CLUSTER_WRITES=1", params=PARAMS,
                          json={"appliedBy": "asesh", "writesEnabled": True},
                          headers={"RESILIENCE_ALLOW_CLUSTER_WRITES": "1"})

    assert refused.status_code == 403 and connected.patches == []
