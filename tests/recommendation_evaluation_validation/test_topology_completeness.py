"""Workloads on a node discovery excludes make the topology incomplete: no trusted verdict may come out of it.

Found on a real Docker Desktop cluster: CoreDNS ran on the control-plane node, which Member 2's discovery leaves
out of the failure domains, so the graph showed the service with 0 ready replicas and Member 4's checker called
a spread INFEASIBLE ("it moves replicas, it does not add them"). That verdict was only a symptom of pods the
graph could not see. The gate that stops this is Member 3's `_require_sufficient_evidence` (reached through
`get_runtime_component_inputs`); these tests pin that Member 4's live pipeline and its approval step both go
through it, so an INFEASIBLE or FEASIBLE verdict is never reported for a service whose pods were not all seen.

The cluster is a fake; no kubeconfig is read.
"""

import pytest

from app.engine.recommendation_evaluation_validation.live_pipeline import build_live_recommendation
from app.shared.evidence_errors import EvidenceError
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
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod


def with_control_plane(db_nodes: list[str], web_nodes=("w2", "w3")) -> FakeClusterSource:
    """db replicas on the named nodes ('cp' is the control-plane node discovery excludes); web is spread."""
    return FakeClusterSource(
        nodes=[node("cp", control_plane=True), node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod(f"db-{i}", name, app="db") for i, name in enumerate(db_nodes)]
                      + [pod(f"web-{i}", name, app="web") for i, name in enumerate(web_nodes)]},
        deployments={"shop": [deployment("db", len(db_nodes)), deployment("web", len(web_nodes))]},
    )


def refused_services(response) -> dict[str, list[str]]:
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "TOPOLOGY_INCOMPLETE"
    return {item["service"]: item["reasons"] for item in detail["details"]["services"]}


def test_a_workflow_service_running_only_on_an_excluded_node_is_refused_not_judged(client, monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(with_control_plane(["cp", "cp"]))

    response = client.get(LIVE, params=PARAMS)

    assert "db" in refused_services(response)
    assert "recommendations" not in response.json()           # nothing, not even an INFEASIBLE entry


def test_a_workflow_service_split_between_a_worker_and_an_excluded_node_is_refused(client, monkeypatch, use_cluster, evidenced_topology):
    # Seen alone, the one replica on w1 looks like a concentrated, single-replica service. It is not.
    freeze_checkout(monkeypatch)
    use_cluster(with_control_plane(["w1", "cp", "cp"]))

    reasons = refused_services(client.get(LIVE, params=PARAMS))

    assert reasons["db"] == ["PODS_ON_EXCLUDED_NODES"]


def test_the_pipeline_raises_the_coded_error_instead_of_returning_a_report(monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(with_control_plane(["w1", "w1", "cp"]))

    with pytest.raises(EvidenceError) as refusal:
        build_live_recommendation("checkout", "w1", "shop")

    assert refusal.value.code == "TOPOLOGY_INCOMPLETE"


def test_a_complete_topology_on_the_same_cluster_is_still_evaluated(client, monkeypatch, use_cluster, evidenced_topology):
    # The control-plane node being present is not itself a problem: only workloads on it are.
    freeze_checkout(monkeypatch)
    use_cluster(with_control_plane(["w1", "w1", "w1"]))

    report = client.get(LIVE, params=PARAMS).json()

    (candidate,) = report["recommendations"]
    assert candidate["service"] == "db" and candidate["feasible"] is True


def test_a_service_the_workflow_does_not_use_does_not_block_it_but_is_never_judged(client, monkeypatch, use_cluster, evidenced_topology):
    cluster = with_control_plane(["w1", "w1", "w1"])
    cluster.pods["shop"] += [pod("dns-0", "cp", app="dns"), pod("dns-1", "cp", app="dns")]
    cluster.deployments["shop"].append(deployment("dns", 2))
    freeze_checkout(monkeypatch)
    use_cluster(cluster)

    response = client.get(LIVE, params=PARAMS)

    assert response.status_code == 200
    judged = {candidate["service"] for candidate in response.json()["recommendations"]}
    assert "dns" not in judged                                 # no INFEASIBLE "0 ready replicas" for dns


def test_a_request_is_not_approved_once_its_service_has_pods_on_an_excluded_node(client, monkeypatch, use_cluster, evidenced_topology):
    freeze_checkout(monkeypatch)
    use_cluster(with_control_plane(["w1", "w1", "w1"]))
    rec_id = client.get(LIVE, params=PARAMS).json()["selectedRecommendationId"]
    assert request_live(client, rec_id).status_code == 200
    use_cluster(with_control_plane(["w1", "w1", "cp"]))           # a replica was rescheduled onto the control-plane

    response = client.post(f"/api/recommendations/{rec_id}/approve", json={"expectedProposalVersion": "v1", "note": ""})

    assert response.status_code == 409 and response.json()["detail"]["code"] == "TOPOLOGY_INCOMPLETE"
    assert client.get(f"/api/recommendations/{rec_id}/approval").json()["status"] == "PENDING_APPROVAL"
