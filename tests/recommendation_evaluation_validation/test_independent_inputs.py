"""Member 4 works without Component 1's frozen workflow, and gets what Component 2 has, changing neither.

Three things are shown here and nothing else: (1) the live path now gets its scheduling facts from Component 2's own data and an
independent taint read, with no test-injected evidence; (2) a workflow a person DECLARED can be evaluated against the live topology
when Component 1 has none; (3) a declared evaluation can never be approved, applied or validated, and nothing in Components 1 to 3 was
used in a way that changes it.
"""

import json
from types import SimpleNamespace

import pytest

from app.engine.recommendation_evaluation_validation import live_pipeline
from app.engine.recommendation_evaluation_validation.approval import (
    DECLARED_WORKFLOW_SOURCE,
    LIVE_EVIDENCE_SOURCE,
    ApprovalModel,
    ApprovalService,
    has_verified_provenance,
)
from app.engine.recommendation_evaluation_validation.declared_workflow import ENV_NAME
from app.engine.recommendation_evaluation_validation.explanation import describe_live_recommendation
from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from app.engine.workflow_dependency_modeling import service as workflow_service
from app.shared.evidence_errors import EvidenceError
from app.shared.schemas.approval import ApprovalStatus
from app.shared.schemas.workflow import WorkflowEdge, WorkflowGraph
from tests.support.fake_cluster import FakeClusterSource, deployment, node, pod
from tests.support.member4_approvals import InMemoryApprovals

DECLARATION = {"workflowId": "checkout", "criticality": 0.9, "declaredBy": "asesh", "mandatoryServices": ["web", "db"],
               "edges": [{"source": "web", "target": "db"}]}


REQUESTS = {"containers": [{"name": "app", "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}}}]}


NO_REQUESTS = {"containers": [{"name": "app", "resources": {}}]}      # containers READ, declaring no requests (stock Bookinfo)


def concentrated_db(requests: bool | None = True) -> FakeClusterSource:
    """db has 3 replicas on w1 and web is spread.

    ``requests=True``: the Deployments declare resource requests. ``False``: their containers were read and declare none (what the stock
    Bookinfo sample looks like). ``None``: their containers were not captured at all.
    """
    spec = REQUESTS if requests else NO_REQUESTS if requests is False else {}
    return FakeClusterSource(
        nodes=[node("w1"), node("w2"), node("w3")],
        pods={"shop": [pod(f"db-{i}", "w1", app="db") for i in range(3)]
                      + [pod("web-1", "w2", app="web"), pod("web-2", "w3", app="web")]},
        deployments={"shop": [deployment("db", 3), deployment("web", 2)]},
        template_specs={"db": spec, "web": spec})


@pytest.fixture
def declared(tmp_path, monkeypatch):
    path = tmp_path / "declared.json"
    path.write_text(json.dumps(DECLARATION))
    monkeypatch.setenv(ENV_NAME, str(path))
    return path


@pytest.fixture
def no_frozen_workflow(monkeypatch):
    """Component 1 has nothing frozen, and asking it for one is itself a failure of this test."""
    def refuse(workflow_id):
        raise AssertionError("the declared path must not ask Component 1 for a frozen workflow")
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph", refuse)


def freeze_checkout(monkeypatch):
    graph = WorkflowGraph(workflow_id="checkout", criticality=0.9, mandatory_services=["web", "db"],
                          edges=[WorkflowEdge(source="web", target="db")])
    monkeypatch.setattr(workflow_service, "get_frozen_workflow_graph",
                        lambda workflow_id: SimpleNamespace(graph=graph, freeze_id="f1", assessment_id="a1"))


# --- (1) the live path gets its facts from Component 2 and the nodes, with nothing injected -------------------------------------------

def test_the_live_path_reaches_a_feasible_verdict_from_component_2s_data_and_a_taint_read_alone(monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())

    live = live_pipeline.build_live_recommendation("checkout", None, "shop")

    verdict = next(v for k, v in live.evidence["feasibility"].items() if k.endswith("-topology-spread"))   # the plain spread; the replica variants have their own
    assert verdict["status"] == "FEASIBLE" and verdict["missingEvidence"] == []
    assert live.report.selected_recommendation_id is not None
    facts = live.evidence["schedulingFacts"]
    assert facts["unavailable"] is None and facts["nodesEnriched"] and facts["servicesWithIntent"] == ["db", "web"]
    assert live.evidence["source"] == LIVE_EVIDENCE_SOURCE


def test_containers_read_with_no_requests_are_a_fact_not_missing_evidence_as_in_stock_bookinfo(monkeypatch, use_cluster):
    """A pod that requests nothing needs no capacity to be scheduled: an observed absence is a fact, so the verdict is reached."""
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db(requests=False))

    live = live_pipeline.build_live_recommendation("checkout", None, "shop")

    verdict = next(v for k, v in live.evidence["feasibility"].items() if k.endswith("-topology-spread"))   # the plain spread; the replica variants have their own
    assert verdict["status"] == "FEASIBLE" and verdict["missingEvidence"] == []


def test_what_was_never_read_stays_missing_and_is_named_never_invented(monkeypatch, use_cluster):
    """Containers that were not captured at all say nothing about requests, and the answer says so instead of assuming zero."""
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db(requests=None))

    live = live_pipeline.build_live_recommendation("checkout", None, "shop")

    verdict = next(v for k, v in live.evidence["feasibility"].items() if k.endswith("-topology-spread"))   # the plain spread; the replica variants have their own
    assert verdict["status"] == "INSUFFICIENT_EVIDENCE"
    assert verdict["missingEvidence"] == ["resource requests of service db (no containers captured)"]
    assert live.report.selected_recommendation_id is None


def test_the_live_path_reports_where_its_scheduling_facts_came_from_or_that_it_could_not_get_them(monkeypatch, use_cluster):
    freeze_checkout(monkeypatch)
    use_cluster(concentrated_db())
    monkeypatch.setattr(live_pipeline, "enrich_scheduling_facts",
                        lambda deployment, namespace, reader=None: (deployment, {"unavailable": "CLUSTER_CONNECTION_FAILED"}))

    live = live_pipeline.build_live_recommendation("checkout", None, "shop")

    assert live.evidence["schedulingFacts"] == {"unavailable": "CLUSTER_CONNECTION_FAILED"}


# --- (2) a declared workflow works without Component 1 -------------------------------------------------------------------------------------

def test_a_declared_workflow_is_evaluated_against_the_live_topology_without_asking_component_1(use_cluster, declared, no_frozen_workflow):
    use_cluster(concentrated_db())

    live = live_pipeline.build_declared_recommendation("checkout", None, "shop")

    assert live.report.workflow_id == "checkout" and live.report.recommendations
    assert live.evidence["source"] == DECLARED_WORKFLOW_SOURCE and live.evidence["workflowFreezeId"] is None
    shown = live.evidence["declaredWorkflow"]
    assert shown["observed"] is False and shown["approved"] is False and shown["declaredBy"] == "asesh"
    assert len(shown["declarationSha256"]) == 64 and "not observed from traces" in shown["limits"]


def test_the_sizing_of_a_declared_failure_is_component_3s_own(use_cluster, declared, no_frozen_workflow, monkeypatch):
    use_cluster(concentrated_db())
    seen = []
    real = live_pipeline.predict_failure_impact
    monkeypatch.setattr(live_pipeline, "predict_failure_impact", lambda *a: seen.append(a[0].workflow_id) or real(*a))

    live = live_pipeline.build_declared_recommendation("checkout", "w1", "shop")

    assert seen and set(seen) == {"checkout"} and live.evidence["failedNode"] == "w1"
    assert live.evidence["blastRadiusBefore"] == pytest.approx(100.0)       # db's three replicas all on w1


def test_the_same_topology_gives_the_same_answer_whether_the_workflow_is_frozen_or_declared(use_cluster, declared, monkeypatch):
    use_cluster(concentrated_db())
    freeze_checkout(monkeypatch)

    frozen = live_pipeline.build_live_recommendation("checkout", "w1", "shop")
    declared_live = live_pipeline.build_declared_recommendation("checkout", "w1", "shop")

    key = lambda live: [(c.type, c.service, c.feasible, c.risk_reduction, c.predicted_blast_radius) for c in live.report.recommendations]
    assert key(frozen) == key(declared_live)


def test_a_declared_workflow_naming_services_the_cluster_does_not_have_is_refused_not_evaluated(use_cluster, tmp_path, monkeypatch, no_frozen_workflow):
    use_cluster(concentrated_db())
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**DECLARATION, "mandatoryServices": ["web", "db", "ghost"]}))
    monkeypatch.setenv(ENV_NAME, str(bad))

    with pytest.raises(EvidenceError) as raised:
        live_pipeline.build_declared_recommendation("checkout", None, "shop")

    assert raised.value.code == "TOPOLOGY_INCOMPLETE" and "ghost" in json.dumps(raised.value.details)


def test_with_no_declaration_nothing_is_evaluated(use_cluster, monkeypatch, no_frozen_workflow):
    use_cluster(concentrated_db())
    monkeypatch.delenv(ENV_NAME, raising=False)

    with pytest.raises(EvidenceError) as raised:
        live_pipeline.build_declared_recommendation("checkout", None, "shop")

    assert raised.value.code == "DECLARED_WORKFLOW_UNAVAILABLE"


# --- (3) a declared evaluation can never be approved, applied or validated ---------------------------------------------------------------------

def test_declared_evidence_is_not_verified_provenance(use_cluster, declared, no_frozen_workflow):
    use_cluster(concentrated_db())
    live = live_pipeline.build_declared_recommendation("checkout", None, "shop")
    approval = ApprovalModel(id=1, recommendation_id="x", proposal_version="v1", status=ApprovalStatus.APPROVED,
                             requested_at=None, requested_by="t", recommendation_snapshot={},
                             evaluation_snapshot={"provenance": dict(live.evidence)})

    assert has_verified_provenance(approval) is False


def test_a_declared_evaluation_that_is_somehow_requested_cannot_be_approved(use_cluster, declared, no_frozen_workflow):
    use_cluster(concentrated_db())
    live = live_pipeline.build_declared_recommendation("checkout", None, "shop")
    selected = live.report.selected_recommendation_id
    assert selected is not None                              # a real candidate: the refusal below is about provenance alone
    service = ApprovalService(InMemoryApprovals())
    service.request_approval(selected, "v1", live.report, requested_by="t", evidence=live.evidence)

    with pytest.raises(Exception) as raised:
        service.approve(selected, "v1", "", decided_by="t", evaluate=lambda _: live)

    assert "APPROVAL_PROVENANCE_UNVERIFIED" in str(raised.value) or "provenance" in str(raised.value).lower()


def test_the_yaml_of_a_declared_evaluation_says_why_it_cannot_be_applied(use_cluster, declared, no_frozen_workflow):
    use_cluster(concentrated_db())
    live = live_pipeline.build_declared_recommendation("checkout", None, "shop")

    proposal = build_yaml_proposal(live, live.report.selected_recommendation_id)

    assert proposal.status == "GENERATED" and proposal.yaml                      # the text exists, to be read
    assert proposal.applicable is False and "declared by a person" in proposal.reason


def test_the_description_names_the_source_so_a_page_can_label_it(use_cluster, declared, no_frozen_workflow):
    use_cluster(concentrated_db())

    shown = describe_live_recommendation(live_pipeline.build_declared_recommendation("checkout", None, "shop"))

    assert shown["evidence"]["source"] == DECLARED_WORKFLOW_SOURCE


def test_the_declared_path_changes_nothing_in_the_cluster_or_in_component_1(use_cluster, declared, no_frozen_workflow):
    source = use_cluster(concentrated_db())

    live_pipeline.build_declared_recommendation("checkout", None, "shop")

    assert all(call.split(":")[0] in ("nodes", "pods", "deployments", "services", "replicasets", "raw", "workload-namespaces")
               for call in source.calls)


@pytest.mark.parametrize("score, text", [(0.7224999999999999, "0.7225"), (0.8, "0.8"), (1.0, "1"), (0.0, "0"), (None, "None")])
def test_a_ranking_score_in_a_sentence_has_no_floating_point_noise(score, text):
    from app.engine.recommendation_evaluation_validation.explanation import _score_text

    assert _score_text(score) == text


def test_the_selection_sentence_uses_it_and_the_score_itself_is_untouched(use_cluster, declared, no_frozen_workflow):
    use_cluster(concentrated_db())

    shown = describe_live_recommendation(live_pipeline.build_declared_recommendation("checkout", None, "shop"))
    selected = next(c for c in shown["candidates"] if c["selected"])

    assert "0000000" not in selected["selectionJustification"] and "9999999" not in selected["selectionJustification"]
    assert selected["score"] == pytest.approx(selected["ranking"]["score"])
