"""The Recommendations page works from the connected cluster and the approved frozen workflows, not typed IDs.

Covers the evidence checklist (`/api/recommendations/context`) and the explained recommendation
(`/api/recommendations/live/detail`): automatic application and workflow selection, which evidence is missing and the
exact next action, feasible and infeasible candidates, "no candidate" never being read as "healthy", and that nothing
here writes to a cluster or fabricates a workflow, a failure-impact result or a recommendation. The cluster is a fake;
no kubeconfig is read.
"""
from types import SimpleNamespace

import pytest

from app.engine.recommendation_evaluation_validation import evidence_context
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.integrations.kubernetes.client import ConnectionFailedError, NotConnectedError
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph
from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)
from tests.recommendation_evaluation_validation.test_live_recommendation import (  # noqa: F401  (fixtures)
    concentrated_db,
    evidenced_topology,
)
from tests.recommendation_evaluation_validation.test_topology_completeness import with_control_plane
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod

CONTEXT = "/api/recommendations/context"
DETAIL = "/api/recommendations/live/detail"
BOOKINFO = ["productpage", "details", "reviews", "ratings"]
BOOKINFO_EDGES = [("productpage", "details"), ("productpage", "reviews"), ("reviews", "ratings")]
READS = {"nodes", "pods", "deployments", "services", "replicasets", "workload-namespaces", "raw"}   # raw: a GET path


def only_reads(cluster) -> bool:
    """Every call the fake cluster saw was a read. (The fake has no write method at all; this pins the call log.)"""
    return bool(cluster.calls) and all(call.split(":")[0] in READS for call in cluster.calls) \
        and not any(verb in call for call in cluster.calls for verb in ("create", "delete", "patch", "update", "apply"))


def bookinfo_cluster() -> FakeClusterSource:
    """Four single-replica services, as the real Bookinfo benchmark runs."""
    placement = ["w1", "w2", "w3", "w1"]
    return FakeClusterSource(
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"bookinfo": [pod(f"{app}-1", placement[i], app=app) for i, app in enumerate(BOOKINFO)]},
        deployments={"bookinfo": [deployment(app, 1) for app in BOOKINFO]},
    )


def frozen(workflow_id, mandatory, edges, freeze_id="freeze-1"):
    graph = WorkflowGraph(workflow_id=workflow_id, criticality=0.9, mandatory_services=list(mandatory),
                          edges=[WorkflowEdge(source=a, target=b) for a, b in edges])
    return SimpleNamespace(graph=graph, freeze_id=freeze_id, assessment_id=f"assessment-{workflow_id}", frozen_at=None)


def store(monkeypatch, *workflows, assessed_but_not_frozen=()):
    """Approved frozen workflows as Component 1 would hold them. Replaces storage reads, nothing else."""
    by_id = {w.graph.workflow_id: w for w in workflows}
    assessments = [SimpleNamespace(workflow_id=w) for w in [*by_id, *assessed_but_not_frozen]]
    monkeypatch.setattr(workflow_service, "list_dependency_assessments", lambda *a, **k: assessments)

    def get(workflow_id, *a, **k):
        if workflow_id not in by_id:
            raise LookupError(f"no frozen graph for {workflow_id}")
        return by_id[workflow_id]

    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", get)


def bookinfo_workflow(workflow_id="bookinfo-productpage"):
    return frozen(workflow_id, BOOKINFO, BOOKINFO_EDGES)


def evidence_item(body, identifier):
    (item,) = [e for e in body["evidence"] if e["id"] == identifier]
    return item


# ---- automatic application and workflow selection ----------------------------------------------------------------

def test_the_only_application_namespace_is_chosen_automatically(client, monkeypatch, use_cluster):
    store(monkeypatch, bookinfo_workflow())
    use_cluster(bookinfo_cluster())

    body = client.get(CONTEXT).json()

    assert (body["namespace"], body["namespaceChoice"]) == ("bookinfo", "only-application")
    assert body["namespaces"][0]["services"] == sorted(BOOKINFO)
    assert body["cluster"]["state"] == "available" and body["cluster"]["fingerprint"] == "fake-connection"
    assert evidence_item(body, "application")["state"] == "available"


def test_with_several_applications_none_is_guessed(client, monkeypatch, use_cluster):
    store(monkeypatch, bookinfo_workflow())
    cluster = bookinfo_cluster()
    cluster.pods["shop"] = [pod("web-1", "w1", app="web")]
    cluster.deployments["shop"] = [deployment("web", 1)]
    use_cluster(cluster)

    body = client.get(CONTEXT).json()

    assert body["namespace"] is None and body["namespaceChoice"] == "ambiguous"
    assert evidence_item(body, "application")["state"] == "missing"
    assert "Choose the application" in " ".join(body["nextActions"])
    assert body["ready"] is False


def test_a_namespace_you_choose_is_used_and_an_unknown_one_is_not_invented(client, monkeypatch, use_cluster):
    store(monkeypatch, bookinfo_workflow())
    use_cluster(bookinfo_cluster())

    chosen = client.get(CONTEXT, params={"namespace": "bookinfo"}).json()
    unknown = client.get(CONTEXT, params={"namespace": "nope"}).json()

    assert (chosen["namespace"], chosen["namespaceChoice"]) == ("bookinfo", "requested")
    assert unknown["namespace"] is None and unknown["namespaceChoice"] == "unknown" and unknown["ready"] is False
    assert "nope" in evidence_item(unknown, "application")["detail"]


def test_the_only_workflow_that_applies_is_selected_for_you(client, monkeypatch, use_cluster):
    store(monkeypatch, bookinfo_workflow(), frozen("checkout", ["web", "db"], [("web", "db")]))
    use_cluster(bookinfo_cluster())

    body = client.get(CONTEXT).json()

    assert (body["workflowId"], body["workflowChoice"], body["ready"]) == ("bookinfo-productpage", "only-applicable", True)
    by_id = {w["workflowId"]: w for w in body["workflows"]}
    assert by_id["bookinfo-productpage"]["applicable"] is True
    # the other workflow is listed, but it names services that are not in this application, and says so
    assert by_id["checkout"]["applicable"] is False
    assert by_id["checkout"]["missingServices"] == ["db", "web"]
    assert "Services not found in bookinfo" in by_id["checkout"]["reason"]


def test_with_several_applicable_workflows_you_choose_and_none_is_guessed(client, monkeypatch, use_cluster):
    store(monkeypatch, bookinfo_workflow("w-a"), bookinfo_workflow("w-b"))
    use_cluster(bookinfo_cluster())

    body = client.get(CONTEXT).json()
    assert body["workflowId"] is None and body["workflowChoice"] == "choose" and body["ready"] is False
    assert [w["workflowId"] for w in body["workflows"] if w["applicable"]] == ["w-a", "w-b"]

    picked = client.get(CONTEXT, params={"workflow_id": "w-b"}).json()
    assert (picked["workflowId"], picked["workflowChoice"], picked["ready"]) == ("w-b", "requested", True)
    # a workflow that does not apply cannot be forced
    forced = client.get(CONTEXT, params={"workflow_id": "not-frozen"}).json()
    assert forced["workflowId"] is None and forced["workflowChoice"] == "unavailable"


def test_a_workflow_that_was_assessed_but_never_frozen_is_not_offered(client, monkeypatch, use_cluster):
    store(monkeypatch, bookinfo_workflow("frozen-one"), assessed_but_not_frozen=("draft-only",))
    use_cluster(bookinfo_cluster())

    ids = [w["workflowId"] for w in client.get(CONTEXT).json()["workflows"]]

    assert ids == ["frozen-one"]


# ---- missing evidence: which one, and the exact next action -----------------------------------------------------

def test_without_a_frozen_workflow_the_page_is_told_exactly_what_to_do(client, monkeypatch, use_cluster):
    store(monkeypatch)  # nothing approved and frozen
    use_cluster(bookinfo_cluster())

    body = client.get(CONTEXT).json()

    workflow = evidence_item(body, "workflow")
    assert workflow["state"] == "missing" and body["workflows"] == [] and body["workflowId"] is None
    assert "freeze its graph" in workflow["nextAction"] and "Workflow dependencies" in workflow["nextAction"]
    assert evidence_item(body, "failureImpact")["state"] == "missing"   # cannot exist without a workflow
    assert body["ready"] is False and workflow["nextAction"] in body["nextActions"]


def test_frozen_workflows_that_do_not_cover_the_application_are_a_different_missing_item(client, monkeypatch, use_cluster):
    store(monkeypatch, frozen("checkout", ["web", "db"], [("web", "db")]))
    use_cluster(bookinfo_cluster())

    workflow = evidence_item(client.get(CONTEXT).json(), "workflow")

    assert workflow["state"] == "missing" and "none covers the services in bookinfo" in workflow["detail"]


def test_an_unreadable_workflow_store_is_reported_without_its_text(client, monkeypatch, use_cluster):
    def broken(*args, **kwargs):
        raise RuntimeError("password=hunter2 at /var/lib/db")

    monkeypatch.setattr(workflow_service, "list_dependency_assessments", broken)
    use_cluster(bookinfo_cluster())

    response = client.get(CONTEXT)

    assert response.status_code == 200
    assert "hunter2" not in response.text and "/var/lib" not in response.text
    assert evidence_item(response.json(), "workflow")["state"] == "missing"


@pytest.mark.parametrize("failure", [NotConnectedError(), ConnectionFailedError("secret-token-xyz")])
def test_without_a_reachable_cluster_the_checklist_says_so_and_leaks_nothing(client, monkeypatch, use_cluster, failure):
    store(monkeypatch, bookinfo_workflow())
    use_cluster(FakeClusterSource(error=failure))

    response = client.get(CONTEXT)

    assert response.status_code == 200 and "secret-token-xyz" not in response.text
    body = response.json()
    assert body["cluster"]["state"] == "missing" and body["namespaces"] == [] and body["namespace"] is None
    assert body["ready"] is False and "Connect" in " ".join(body["nextActions"])


def test_an_incompletely_resolved_topology_makes_the_workflow_not_applicable_and_says_which_service(client, monkeypatch, use_cluster):
    store(monkeypatch, frozen("checkout", ["web", "db"], [("web", "db")]))
    use_cluster(with_control_plane(["cp", "cp"]))   # db pods run where discovery cannot see them

    body = client.get(CONTEXT, params={"namespace": "shop"}).json()

    assert evidence_item(body, "topology")["state"] == "incomplete"
    (workflow,) = body["workflows"]
    assert workflow["applicable"] is False and "db" in workflow["incompleteServices"]
    assert body["ready"] is False


def test_the_checklist_is_read_only_and_builds_no_workflow(client, monkeypatch, use_cluster):
    store(monkeypatch)
    cluster = bookinfo_cluster()
    use_cluster(cluster)

    body = client.get(CONTEXT).json()

    assert body["readOnly"] is True and body["workflows"] == []          # nothing fabricated
    assert only_reads(cluster)


# ---- the explained recommendation --------------------------------------------------------------------------------

def test_bookinfo_gets_no_invented_replica_fix(client, monkeypatch, use_cluster, evidenced_topology):
    store(monkeypatch, bookinfo_workflow())
    use_cluster(bookinfo_cluster())

    response = client.get(DETAIL, params={"workflow_id": "bookinfo-productpage", "namespace": "bookinfo"})

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"]["state"] == "CANDIDATES_NOT_READY"
    assert body["applied"] is False and body["report"]["selectedRecommendationId"] is None
    assert body["candidates"], "the services on the riskiest node are evaluated"
    for candidate in body["candidates"]:
        assert candidate["feasible"] is False and candidate["selected"] is False
        assert candidate["approval"]["ready"] is False and candidate["approval"]["reason"]
        assert candidate["declaredReplicas"] == 1
        assert any("single replica" in note for note in candidate["notes"])
        # no improvement is claimed: the predicted blast radius is the baseline
        assert candidate["predicted"]["riskReduction"] == 0.0
        assert candidate["predicted"]["blastRadiusAfter"] == candidate["predicted"]["blastRadiusBefore"]
        assert candidate["simulatedDeployment"] is None             # no predicted placement exists for an infeasible change
        assert candidate["feasibility"]["status"] == "INFEASIBLE"
        assert candidate["rootCause"] and candidate["recommendedChange"] and candidate["currentPlacement"]
    assert body["yaml"]["available"] is False and "invented" in body["yaml"]["reason"]


def test_a_concentrated_service_is_shown_with_everything_the_page_needs(client, monkeypatch, use_cluster, evidenced_topology):
    store(monkeypatch, frozen("checkout", ["web", "db"], [("web", "db")]))
    use_cluster(concentrated_db())

    body = client.get(DETAIL, params={"workflow_id": "checkout", "namespace": "shop"}).json()

    assert body["outcome"]["state"] == "READY"
    (candidate,) = body["candidates"]
    assert (candidate["service"], candidate["type"], candidate["feasible"], candidate["selected"]) == ("db", "TOPOLOGY_SPREAD", True, True)
    assert candidate["approval"] == {"ready": True, "reason": None, "requiresJustification": False}
    assert "topologySpreadConstraint" in candidate["recommendedChange"]
    assert candidate["rootCause"]
    assert [p["node"] for p in candidate["currentPlacement"]["db"]] == ["w1", "w1", "w1"]     # the failure-domain problem
    assert candidate["predicted"]["blastRadiusBefore"] == pytest.approx(100.0)
    assert candidate["predicted"]["blastRadiusAfter"] == pytest.approx(0.0)
    assert candidate["cost"]["resource"] == "LOW" and candidate["cost"]["note"]
    assert candidate["selectionJustification"].startswith("Selected: top-ranked feasible candidate")
    assert candidate["feasibility"]["status"] == "FEASIBLE"
    # the predicted placement is the engine's own, a valid DeploymentGraph, and a different one from today's
    assert candidate["simulatedDeployment"]["services"]
    assert body["currentDeployment"]["nodes"]
    assert body["evidence"]["workflowFreezeId"] == "freeze-1" and body["evidence"]["isPrediction"] is True
    assert body["yaml"]["available"] is True and body["yaml"]["applicable"] is True
    assert "topologySpreadConstraints" in body["yaml"]["text"] and "NOT APPLIED" in body["yaml"]["text"]
    assert body["yaml"]["podReplacement"]["included"] is False
    assert candidate["yamlProposal"]["status"] == "GENERATED"


def test_no_candidate_is_never_reported_as_a_healthy_system(client, monkeypatch, use_cluster, evidenced_topology):
    store(monkeypatch, frozen("checkout", ["web", "db"], [("web", "db")]))
    use_cluster(FakeClusterSource(   # every service already spread over three workers: nothing for the engine to change
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod(f"db-{i}", f"w{i + 1}", app="db") for i in range(3)] + [pod(f"web-{i}", f"w{i + 1}", app="web") for i in range(3)]},
        deployments={"shop": [deployment("db", 3), deployment("web", 3)]},
    ))

    body = client.get(DETAIL, params={"workflow_id": "checkout", "namespace": "shop"}).json()

    assert body["candidates"] == [] and body["outcome"]["state"] == "NO_CANDIDATES"
    assert "not a finding that the application is resilient" in body["outcome"]["message"]
    assert body["applied"] is False


def test_missing_evidence_keeps_its_own_coded_error(client, monkeypatch, use_cluster, evidenced_topology):
    store(monkeypatch)
    use_cluster(bookinfo_cluster())

    no_workflow = client.get(DETAIL, params={"namespace": "bookinfo"})
    not_frozen = client.get(DETAIL, params={"workflow_id": "ghost", "namespace": "bookinfo"})

    assert no_workflow.status_code == 409 and no_workflow.json()["detail"]["code"] == "RECOMMENDATION_EVIDENCE_UNAVAILABLE"
    assert not_frozen.status_code == 404 and not_frozen.json()["detail"]["code"] == "WORKFLOW_EVIDENCE_UNAVAILABLE"


def test_incomplete_failure_impact_evidence_names_the_services_and_reasons(client, monkeypatch, use_cluster, evidenced_topology):
    store(monkeypatch, frozen("checkout", ["web", "db"], [("web", "db")]))
    use_cluster(with_control_plane(["cp", "cp"]))

    response = client.get(DETAIL, params={"workflow_id": "checkout", "namespace": "shop"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "TOPOLOGY_INCOMPLETE"
    assert any(entry["service"] == "db" for entry in detail["details"]["services"])


def test_an_unknown_failed_node_is_refused_not_guessed(client, monkeypatch, use_cluster, evidenced_topology):
    store(monkeypatch, bookinfo_workflow())
    use_cluster(bookinfo_cluster())

    response = client.get(DETAIL, params={"workflow_id": "bookinfo-productpage", "namespace": "bookinfo", "failed_node": "no-such-node"})

    assert response.status_code == 422 and response.json()["detail"]["code"] == "INVALID_RECOMMENDATION_REQUEST"


def test_the_detail_reads_only_and_does_not_change_the_stored_evidence_or_the_live_contract(client, monkeypatch, use_cluster, evidenced_topology):
    from app.engine.recommendation_evaluation_validation.live_pipeline import build_live_recommendation

    store(monkeypatch, frozen("checkout", ["web", "db"], [("web", "db")]))
    cluster = concentrated_db()
    use_cluster(cluster)
    params = {"workflow_id": "checkout", "namespace": "shop"}

    live = client.get("/api/recommendations/live", params=params)
    detail = client.get(DETAIL, params=params)

    assert live.status_code == detail.status_code == 200
    assert live.json() == detail.json()["report"], "the shared RecommendationReport contract is unchanged"
    recomputed = build_live_recommendation("checkout", None, "shop")
    # what an approval stores is unchanged: the display detail lives beside the evidence, never in it
    assert "currentDeployment" not in recomputed.evidence and "candidates" not in recomputed.evidence
    assert recomputed.details["currentDeployment"] and recomputed.details["candidates"]
    assert only_reads(cluster)
